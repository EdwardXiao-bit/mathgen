/**
 * 老师端 REST 客户端。
 *
 * 设计要点：
 * 1. **不猜字段**：类型来自 `api.ts`（后端冻结契约的镜像）。
 * 2. **失败要可读**：后端未启动时 `fetch` 抛 `TypeError`，这里统一包装成 `ApiError`，
 *    由界面渲染成明确的中文提示而不是白屏（任务卡硬要求）。
 * 3. **不引入任何外部依赖**：没有 axios、没有 CDN，产物可离线运行（NFR-9）。
 * 4. 每个请求都带 `AbortController` 超时，避免后端半死不活时界面永久转圈。
 */

import {
  route,
  type AnswerLayoutIn,
  type AttemptDetailOut,
  type AttemptExportQuery,
  type AttemptItemOut,
  type AttemptListOut,
  type AttemptListQuery,
  type AttemptSummary,
  type ErrorResponse,
  type LayoutIn,
  type PaperConfigIn,
  type PaperDetail,
  type PaperItemOut,
  type PaperListOut,
  type PaperListQuery,
  type PublishResult,
  type SheetMode,
} from './api';

/** 出题请求体 —— `POST /api/papers`（契约 §5.1：平铺的 `PaperConfigIn` + `seed?` / `title?`）。 */
export interface CreatePaperRequest {
  config: PaperConfigIn;
  seed?: number;
  title?: string;
  /**
   * 第六维答案版形式。
   *
   * 它不是 `PaperConfigIn` 的字段（契约里 `AnswerLayoutIn` 是独立模型），所以按契约**不该**出现在
   * 创建请求体里。但创建表单上用户已经勾了四个开关，直接丢掉等于「选了不生效」。
   * 于是：先尝试带在创建请求里（后端若支持就一次搞定）；被拒（422）则退回
   * 「创建后再 `PATCH .../answer-layout`」——两条路都让用户的选择**真的落库**，
   * 具体走哪条由 `createPaper` 回传，界面如实显示。
   */
  answerLayout?: AnswerLayoutIn;
}

/** `listPapers` 的返回值：附上「后端用的是哪套分页参数形态」的探测结果。 */
export interface ListPapersResult {
  data: PaperListOut;
  /** 'contract' = limit/offset 生效；'legacy' = 后端还是 page/size，前端做了换算 */
  paginationMode: 'contract' | 'legacy';
}

/** `createPaper` 的返回值：附上答案版形式是怎么落库的。 */
export interface CreatePaperResult {
  paper: PaperDetail;
  /** 'in-create' = 创建请求就带上了；'follow-up-patch' = 创建后补一次 PATCH；'not-requested' = 用户没改（用默认） */
  answerLayoutMode: 'in-create' | 'follow-up-patch' | 'not-requested';
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }

  /** 网络层失败：后端没起来 / 代理连不上 / 超时。 */
  get isOffline(): boolean {
    return this.code === 'NETWORK_UNREACHABLE';
  }

  /**
   * 「后端根本没应答」——包括网络层失败，以及**网关类**状态码。
   *
   * 为什么需要它：真实部署里前端走 Vite dev proxy（或任意反向代理），后端没启动时
   * 浏览器拿到的是代理产生的 500/502/503，而不是 `fetch` 直接抛错。
   * 这时如果说成「后端已响应」，提示就把老师指向错误的方向。
   */
  get isBackendUnreachable(): boolean {
    return this.isOffline || [500, 502, 503, 504].includes(this.status);
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = (value as { error?: unknown }).error;
  if (typeof candidate !== 'object' || candidate === null) return false;
  const { code, message } = candidate as { code?: unknown; message?: unknown };
  return typeof code === 'string' && typeof message === 'string';
}

/**
 * 发请求并返回**解析后的 JSON（或原文）与元信息**。
 * 需要看状态码 / Content-Type 的调用方（例如取卷面 HTML）用这个；其余用 `requestJson`。
 */
async function fetchRawDetails(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<{ value: unknown; status: number; contentType: string; text: string }> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === 'AbortError';
    throw new ApiError(
      'NETWORK_UNREACHABLE',
      aborted
        ? `请求超时（>${Math.round(timeoutMs / 1000)} 秒）：${path}。请确认后端已在 127.0.0.1:8000 启动。`
        : `连接不上后端：${path}。请在后端目录执行 uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend 后重试。`,
      0,
    );
  } finally {
    window.clearTimeout(timer);
  }

  const text = await response.text();
  const contentType = response.headers.get('content-type') ?? '';
  let parsed: unknown = null;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    if (isErrorResponse(parsed)) {
      throw new ApiError(parsed.error.code, parsed.error.message, response.status);
    }
    throw new ApiError(
      `HTTP_${response.status}`,
      text.length > 0
        ? `后端返回 ${response.status}：${text.slice(0, 300)}`
        : `后端返回 ${response.status}，且响应体为空`,
      response.status,
    );
  }

  return { value: parsed, status: response.status, contentType, text };
}

/** 只要 JSON 的调用方用这个。 */
async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const { value } = await fetchRawDetails(path, init, timeoutMs);
  return value as T;
}

/** `POST /api/papers` 的响应。
 *
 * 契约 §5.1 写的是「关键响应字段 `paper_id`, `status`, `items[]`」，未钉死外层包装，
 * 所以这里两种形态都接受：裸 `PaperDetail`，或 `{ paper: PaperDetail }` / `{ ...detail }`。
 * 解析失败时抛出可读错误，而不是让界面在 `undefined.id` 上崩掉。
 */
export function parsePaperDetail(payload: unknown): PaperDetail {
  if (typeof payload !== 'object' || payload === null) {
    throw new ApiError('BAD_RESPONSE', '后端返回的卷数据结构无法识别（不是对象）', 200);
  }
  const direct = payload as Partial<PaperDetail>;
  if (typeof direct.id === 'number' && Array.isArray(direct.items)) {
    return direct as PaperDetail;
  }
  const nested = (payload as { paper?: unknown }).paper;
  if (typeof nested === 'object' && nested !== null) {
    const inner = nested as Partial<PaperDetail>;
    if (typeof inner.id === 'number' && Array.isArray(inner.items)) {
      return inner as PaperDetail;
    }
  }
  throw new ApiError(
    'BAD_RESPONSE',
    '后端返回的卷数据结构无法识别：期望 PaperDetail（含 id / items），实际缺少必需字段',
    200,
  );
}

/**
 * 分页参数形态探测结果缓存。
 *
 * 为什么按「基地址」缓存：同一份产物可能先连旧后端、后连新后端（或反之），
 * 用模块级常量会把第一次的结果永久固化；按 origin 缓存则换后端会重新探测。
 */
const paginationModeCache = new Map<string, 'contract' | 'legacy'>();

/** 测试与「后端刚升级」时用：清掉探测缓存。 */
export function resetPaginationModeCache(): void {
  paginationModeCache.clear();
}

function apiBase(): string {
  return typeof window === 'undefined' ? 'http://127.0.0.1:8000' : window.location.origin;
}

/**
 * 探测后端用哪套分页参数：契约的 `limit`/`offset`，还是历史实现的 `page`/`size`。
 *
 * 判据是**响应回显**，不是「请求没报错」——对未知查询参数后端是忽略，不报错。
 * 所以这里要求 `limit=1` 既被回显成 `limit: 1`，又真的只返回 1 条，才算契约形态生效。
 * 探测失败（后端没起）时**不写缓存**，下次请求再探，避免把「暂时连不上」误判成旧形态。
 */
async function detectPaginationMode(): Promise<'contract' | 'legacy'> {
  const cached = paginationModeCache.get(apiBase());
  if (cached) return cached;

  const probeUrl = `${route('list_papers')}?limit=1&offset=0`;
  try {
    const payload = (await requestJson<unknown>(probeUrl)) as {
      limit?: unknown;
      papers?: unknown;
    };
    const echoesLimit = payload?.limit === 1;
    const count = Array.isArray(payload?.papers) ? payload.papers.length : -1;
    const mode: 'contract' | 'legacy' = echoesLimit && count <= 1 ? 'contract' : 'legacy';
    paginationModeCache.set(apiBase(), mode);
    return mode;
  } catch {
    return 'legacy';
  }
}

/**
 * 把列表响应归一成 `PaperListOut`。
 *
 * 两种形态都要接受：
 *   - 契约：`{total, limit, offset, papers}`
 *   - 历史：`{total, papers}`（`page`/`size` 语义）——此时 limit/offset 由调用方补回，
 *     否则界面拿 `undefined` 去算分页会显示成 NaN。
 * 缺 `papers` 数组一律抛 BAD_RESPONSE，而不是让界面崩在 `.map` 上。
 */
function normalizePaperList(
  payload: unknown,
  limit: number,
  offset: number,
  mode: 'contract' | 'legacy',
): PaperListOut {
  if (typeof payload !== 'object' || payload === null) {
    throw new ApiError('BAD_RESPONSE', '卷库响应不是对象', 200);
  }
  const raw = payload as Partial<PaperListOut>;
  if (!Array.isArray(raw.papers)) {
    throw new ApiError('BAD_RESPONSE', '卷库响应缺少 papers 数组', 200);
  }
  const total = typeof raw.total === 'number' ? raw.total : raw.papers.length;
  return {
    total,
    limit: typeof raw.limit === 'number' ? raw.limit : limit,
    offset: typeof raw.offset === 'number' ? raw.offset : mode === 'legacy' ? offset : 0,
    // D78 两字段对**未实现归档的旧后端**不存在：补上默认值而不是留 `undefined`，
    // 否则界面会把「字段缺失」当成「未知状态」渲染（`undefined` 上界面是 A10 明令禁止的）。
    // 语义上缺失 = 未归档 / 无成绩，与后端的默认值一致。
    papers: raw.papers.map((paper) => ({
      ...paper,
      archived_at: paper.archived_at ?? null,
      has_attempts: paper.has_attempts ?? false,
    })),
  };
}

// ---------------------------------------------------------------------------
// M5 成绩（FR-T11 / FR-T12 / FR-T13）的响应归一
// ---------------------------------------------------------------------------

/**
 * 把一行成绩归一成 `AttemptSummary`。
 *
 * 两条规矩：
 * 1. **`null` 不许变成 `undefined`**（A10 明令禁止渲染 `undefined`）。`paper_id` /
 *    `paper_code` / `paper_title` 为 `null` 是**合法状态**（自主练习，D75），
 *    界面据此显示「自主练习」；后端若整个键缺失（旧后端），补齐成同样的 `null`，
 *    语义与契约默认值一致。
 * 2. **`score` 缺失时抛错，不兜底算一遍**。兜底就意味着前端保留第二套得分算法，
 *    而那正是 D80 要消灭的东西（两套口径会在 `(40, 23)` 这类点上分叉出 57 / 58，
 *    且不会自己报错）。宁可让页面显示一条可读的错误。
 */
function normalizeAttempt(raw: unknown): AttemptSummary {
  if (typeof raw !== 'object' || raw === null) {
    throw new ApiError('BAD_RESPONSE', '成绩行不是对象', 200);
  }
  const row = raw as Partial<AttemptSummary>;
  if (typeof row.score !== 'number') {
    throw new ApiError(
      'BAD_RESPONSE',
      '成绩行缺少 score 字段：得分只能由后端给出（D80），前端不会自己算一遍',
      200,
    );
  }
  return {
    ...(row as AttemptSummary),
    paper_id: row.paper_id ?? null,
    paper_code: row.paper_code ?? null,
    paper_title: row.paper_title ?? null,
  };
}

/** 把列表响应归一成 `AttemptListOut`；缺 `attempts` 数组一律抛错，不让界面崩在 `.map` 上。 */
function normalizeAttemptList(
  payload: unknown,
  limit: number,
  offset: number,
): AttemptListOut {
  if (typeof payload !== 'object' || payload === null) {
    throw new ApiError('BAD_RESPONSE', '成绩列表响应不是对象', 200);
  }
  const raw = payload as Partial<AttemptListOut>;
  if (!Array.isArray(raw.attempts)) {
    throw new ApiError('BAD_RESPONSE', '成绩列表响应缺少 attempts 数组', 200);
  }
  const attempts = raw.attempts.map(normalizeAttempt);
  return {
    total: typeof raw.total === 'number' ? raw.total : attempts.length,
    limit: typeof raw.limit === 'number' ? raw.limit : limit,
    offset: typeof raw.offset === 'number' ? raw.offset : offset,
    attempts,
  };
}

/** 把单题明细归一成 `AttemptItemOut`（`student_answer` 为 `null` = 未作答）。 */
function normalizeAttemptItem(raw: unknown): AttemptItemOut {
  if (typeof raw !== 'object' || raw === null) {
    throw new ApiError('BAD_RESPONSE', '成绩明细里的题目不是对象', 200);
  }
  const item = raw as Partial<AttemptItemOut>;
  return {
    ...(item as AttemptItemOut),
    student_answer: item.student_answer ?? null,
  };
}

/**
 * 把明细响应归一成 `AttemptDetailOut`。
 *
 * 额外断言 `total === items.length`：契约说这条等式由后端**写路径**保证（不是用
 * `len(items)` 凑出来的）。两处对不上时，页面上「答对 7/10」与列出的 10 行会自相矛盾，
 * 而且**不会自己报错** —— 所以在这里变成一条可读的错误。
 */
function normalizeAttemptDetail(payload: unknown): AttemptDetailOut {
  const summary = normalizeAttempt(payload);
  const items = (payload as { items?: unknown }).items;
  if (!Array.isArray(items)) {
    throw new ApiError('BAD_RESPONSE', '成绩明细响应缺少 items 数组', 200);
  }
  const normalized = items.map(normalizeAttemptItem);
  if (normalized.length !== summary.total) {
    throw new ApiError(
      'BAD_RESPONSE',
      `成绩明细自相矛盾：总题数写的是 ${summary.total}，逐题明细却只有 ${normalized.length} 条`,
      200,
    );
  }
  return { ...summary, items: normalized };
}

/**
 * 从 `Content-Disposition` 里取文件名。
 *
 * 后端**固定给两条**（`app/export/attempt_export.py:content_disposition`）：
 * `filename="<ASCII 兜底>"` 与 `filename*=UTF-8''<百分号编码>`。因为文件名总是含中文
 * （`成绩-YYYYMMDD-HHMM.csv`），ASCII 兜底会把「成绩」换成 `_`，所以**优先**用 `filename*`。
 */
function filenameFromDisposition(header: string | null): string {
  if (!header) return '';
  const extended = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      // 百分号编码坏了就走下面的 ASCII 兜底，不抛错
    }
  }
  const plain = /filename="([^"]*)"/i.exec(header);
  return plain ? plain[1] : '';
}

/**
 * 取**文件流**（`GET /api/attempts/export`）。
 *
 * 不复用 `fetchRawDetails`：那个函数用 `response.text()` 读正文，而 xlsx 是二进制
 * （ZIP 容器），按文本读一遍再编码回去会**直接损坏文件**。这里成功时取 `Blob`，
 * 失败时仍按 JSON 错误体解析 —— 导出失败必须让人看见后端给的**真正原因**
 * （D65：报错好过静默错；界面不许只弹一句「操作失败」）。
 */
async function fetchAttachment(
  path: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<{ blob: Blob; filename: string; contentType: string }> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'GET',
      signal: controller.signal,
      headers: { Accept: '*/*' },
    });
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === 'AbortError';
    throw new ApiError(
      'NETWORK_UNREACHABLE',
      aborted
        ? `导出超时（>${Math.round(timeoutMs / 1000)} 秒）：${path}`
        : `导出时连接不上后端：${path}。请确认后端已启动后重试。`,
      0,
    );
  } finally {
    window.clearTimeout(timer);
  }

  if (!response.ok) {
    const text = await response.text();
    let parsed: unknown = null;
    if (text.length > 0) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
    }
    if (isErrorResponse(parsed)) {
      throw new ApiError(parsed.error.code, parsed.error.message, response.status);
    }
    throw new ApiError(
      `HTTP_${response.status}`,
      text.length > 0
        ? `导出失败：后端返回 ${response.status}：${text.slice(0, 300)}`
        : `导出失败：后端返回 ${response.status}，且没有给出原因`,
      response.status,
    );
  }

  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get('content-disposition')),
    contentType: response.headers.get('content-type') ?? '',
  };
}

export const teacherApi = {
  /** `POST /api/papers` —— 生成新卷（FR-T1 / FR-T2）。
   *
   * D55（已冻结）：单资源参数**平铺** —— body 就是 `PaperConfigIn` 的字段，
   * `seed` / `title` 走可选查询参数。旧契约措辞写的 `{config, seed, title}` 已被判定为文档缺陷，
   * 所以这里只有一条路径，没有兼容分支。
   *
   * 第六维 `answer_layout` 不在 `PaperConfigIn` 里，所以按契约不该出现在创建 body；
   * 但用户在创建表单上已经勾了开关，不能丢掉（那就是「选了不生效」）。做法见
   * `CreatePaperRequest.answerLayout` 的注释：先带上，被拒就补一次 PATCH。
   */
  async createPaper(body: CreatePaperRequest): Promise<CreatePaperResult> {
    const { config, seed, title, answerLayout } = body;
    const query = new URLSearchParams();
    if (seed !== undefined) query.set('seed', String(seed));
    if (title !== undefined) query.set('title', title);
    const search = query.toString();
    const url = route('create_paper') + (search ? `?${search}` : '');

    const requestBody: Record<string, unknown> = { ...config };
    if (answerLayout !== undefined) requestBody.answer_layout = answerLayout;

    try {
      const payload = await requestJson<unknown>(url, {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });
      return {
        paper: parsePaperDetail(payload),
        answerLayoutMode: answerLayout === undefined ? 'not-requested' : 'in-create',
      };
    } catch (error) {
      // 只有「请求体形状不被接受」才回退；其它错误（离线 / 冲突 / 无解）照原样抛
      const shapeRejected =
        answerLayout !== undefined &&
        error instanceof ApiError &&
        (error.code === 'VALIDATION_ERROR' || error.code === 'CONFIG_INVALID');
      if (!shapeRejected) throw error;
      const payload = await requestJson<unknown>(url, {
        method: 'POST',
        body: JSON.stringify(config),
      });
      const paper = parsePaperDetail(payload);
      const saved = await teacherApi.updateAnswerLayout(paper.id, answerLayout as AnswerLayoutIn);
      return {
        paper: { ...paper, answer_layout: saved },
        answerLayoutMode: 'follow-up-patch',
      };
    }
  },

  /**
   * `GET /api/papers` —— 卷库（FR-T14），M3 起带分页 / 搜索 / 状态筛选。
   *
   * **形状自适应**（与 D55 同源的契约-实现漂移处理，但不猜、不静默）：
   * 契约 §5.1 的参数是 `limit` / `offset`；已交付的后端路由曾用 `page` / `size`。
   * 两者**语义不同**，而且对未知查询参数后端通常是**忽略**——所以盲目发 `limit`
   * 会拿到「永远是第 1 页的 20 条」，界面会显示错误的分页内容。
   * 因此这里先探测一次（`limit=1`）：
   *   - 响应回显 `limit` 且只返回 1 条 → 契约形态生效，按 limit/offset 走
   *   - 否则 → 回退 `page` / `size`（offset 换算成 page），并把模式回传给界面
   * 探测结果按「基地址」缓存，不每次请求都多打一发。
   */
  async listPapers(query?: Partial<PaperListQuery>): Promise<ListPapersResult> {
    const limit = query?.limit ?? 20;
    const offset = query?.offset ?? 0;
    const q = query?.q?.trim() ?? '';
    const status = query?.status;
    const archived = query?.archived;

    const buildUrl = (mode: 'contract' | 'legacy') => {
      const params = new URLSearchParams();
      if (mode === 'contract') {
        params.set('limit', String(limit));
        params.set('offset', String(offset));
      } else {
        params.set('size', String(limit));
        params.set('page', String(Math.floor(offset / Math.max(1, limit)) + 1));
      }
      if (q !== '') params.set('q', q);
      if (status !== undefined) params.set('status', status);
      // D78：不传 `archived` 就是后端的 `exclude` 默认值，**不要**在这里补默认值——
      // 显式发 `archived=exclude` 会让「旧后端忽略未知参数」这件事无法被区分：
      // 忽略的参数与后端自己的默认值恰好同义，所以两者都不会改变结果。
      if (archived !== undefined) params.set('archived', archived);
      return `${route('list_papers')}?${params.toString()}`;
    };

    const mode = await detectPaginationMode();
    const payload = await requestJson<unknown>(buildUrl(mode));
    return { data: normalizePaperList(payload, limit, offset, mode), paginationMode: mode };
  },

  /**
   * `GET /api/papers/by-code/{code}` —— **按卷号**取卷（FR-T15）。
   *
   * 明确不走「翻列表找卷号」：那是分页里翻不到的卷就永远打不开。
   * 卷号不存在时后端返回 404 `CODE_NOT_FOUND`，由界面渲染成可读提示。
   */
  async getPaperByCode(code: string): Promise<PaperDetail> {
    const payload = await requestJson<unknown>(
      route('get_paper_by_code', { code: code.trim() }),
    );
    return parsePaperDetail(payload);
  },

  /** `GET /api/papers/{id}` —— 卷详情（含题目快照）。 */
  async getPaper(paperId: number): Promise<PaperDetail> {
    const payload = await requestJson<unknown>(route('get_paper', { paper_id: paperId }));
    return parsePaperDetail(payload);
  },

  /** `DELETE /api/papers/{id}/items/{seq}` —— 删单题；返回剩余题目（题号已前移）。 */
  async deleteItem(paperId: number, seq: number): Promise<PaperItemOut[]> {
    const payload = await requestJson<PaperItemOut[] | { items: PaperItemOut[] }>(
      route('delete_item', { paper_id: paperId, seq }),
      { method: 'DELETE' },
    );
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    throw new ApiError('BAD_RESPONSE', '删题响应缺少 items 数组', 200);
  },

  /**
   * `POST /api/papers/{id}/archive` —— **归档**（D78 软删除，契约 §5.1）。
   *
   * 与 `status` **正交**：归档不改 `status`，恢复后回到原来的草稿 / 已发布。
   * 任何状态的卷都能归档，且**不影响学生端**（纸张已印、二维码已发，把旧卷突然
   * 变成打不开只会让正在上课的人踩坑）。可逆，所以界面不做二次确认。
   */
  async archivePaper(paperId: number): Promise<PaperDetail> {
    const payload = await requestJson<unknown>(route('archive_paper', { paper_id: paperId }), {
      method: 'POST',
    });
    return parsePaperDetail(payload);
  },

  /** `POST /api/papers/{id}/unarchive` —— **恢复**：把卷放回「在用」列表（可逆）。 */
  async unarchivePaper(paperId: number): Promise<PaperDetail> {
    const payload = await requestJson<unknown>(route('unarchive_paper', { paper_id: paperId }), {
      method: 'POST',
    });
    return parsePaperDetail(payload);
  },

  /**
   * `DELETE /api/papers/{id}` —— **物理删除**（D78），成功是 **204 且无响应体**。
   *
   * 该卷已有学生成绩时后端返回 **409 `PAPER_HAS_ATTEMPTS`**。
   * 界面**必须**先用 `PaperSummary.has_attempts` 事先禁用入口并写明理由；
   * 这里的 409 只是**并发**下的最后一道防线（列表读完之后、点删除之前刚好有学生交了卷），
   * 两道都要有 —— 但不能只有后一道（那就是「点下去才吃错误」，D65/D59 明确要防的）。
   */
  async deletePaper(paperId: number): Promise<void> {
    await requestJson<null>(route('delete_paper', { paper_id: paperId }), { method: 'DELETE' });
  },

  /** `POST /api/papers/{id}/items/{seq}/regenerate` —— 换一题。 */
  async regenerateItem(paperId: number, seq: number): Promise<PaperItemOut> {
    const payload = await requestJson<PaperItemOut | { item: PaperItemOut }>(
      route('regenerate_item', { paper_id: paperId, seq }),
      { method: 'POST' },
    );
    if (payload && typeof payload === 'object' && 'item' in payload) {
      return (payload as { item: PaperItemOut }).item;
    }
    return payload as PaperItemOut;
  },

  /**
   * `PATCH /api/papers/{id}/answer-layout` —— 第六维「答案版形式」落库。
   *
   * body 是 `AnswerLayoutIn` 字段**平铺**（与 D55 的排版一致，不是包一层）。
   * 已发布的卷只读：后端返回 **409 `PAPER_NOT_EDITABLE`**，这里原样抛给界面，
   * 由界面明确提示「已发布不可改」——**不允许静默失败**（D59 的教训）。
   */
  updateAnswerLayout(paperId: number, answerLayout: AnswerLayoutIn): Promise<AnswerLayoutIn> {
    return requestJson<AnswerLayoutIn>(route('update_answer_layout', { paper_id: paperId }), {
      method: 'PATCH',
      body: JSON.stringify(answerLayout),
    });
  },

  /** `PATCH /api/papers/{id}/layout` —— 持久化排版（预览本身不等它）。
   *
   * D55（已冻结）：body 是 `LayoutIn` 字段**平铺**，不是 `{"layout": {...}}` 包一层。
   */
  updateLayout(paperId: number, layout: LayoutIn): Promise<LayoutIn> {
    return requestJson<LayoutIn>(route('update_layout', { paper_id: paperId }), {
      method: 'PATCH',
      body: JSON.stringify(layout),
    });
  },

  /**
   * `GET /api/papers/{id}/sheet?mode=` —— **卷面 HTML 的唯一来源**（D56 / C3）。
   *
   * 预览 iframe 直接把这个 URL 作为 src；这里的方法供打印通道取 HTML 用。
   * 返回的是完整 HTML 文档（后端 `render_sheet` 产物），前端不做任何再排版。
   */
  async getSheetHtml(paperId: number, mode: SheetMode): Promise<string> {
    const url = `${route('sheet', { paper_id: paperId })}?mode=${encodeURIComponent(mode)}`;
    const { contentType, text } = await fetchRawDetails(url, { method: 'GET' }, 30_000);
    if (!contentType.includes('text/html')) {
      // 后端渲染模块未就绪时返回 503 + JSON（error.code = RENDER_NOT_READY）；
      // 这类响应不能塞进 iframe（会显示一段 JSON），必须抛成可读错误。
      throw new ApiError(
        'SHEET_NOT_HTML',
        `卷面接口返回的不是 HTML（Content-Type: ${contentType || '空'}）：${text.slice(0, 200)}`,
        200,
      );
    }
    return text;
  },

  /** `POST /api/papers/{id}/publish` —— 发卷（FR-T10），返回卷号与二维码地址。 */
  publishPaper(paperId: number): Promise<PublishResult> {
    return requestJson<PublishResult>(route('publish_paper', { paper_id: paperId }), {
      method: 'POST',
    });
  },

  /**
   * `POST /api/papers/{id}/copy` —— 另存为可编辑副本（D53 / D32）。
   * 已发布卷只读，改题只能走这条路；返回新 draft（新 id、无 code）。
   */
  async copyPaper(paperId: number): Promise<PaperDetail> {
    const payload = await requestJson<unknown>(route('copy_paper', { paper_id: paperId }), {
      method: 'POST',
    });
    return parsePaperDetail(payload);
  },

  // -------------------------------------------------------------------------
  // M5 成绩（FR-T11 / FR-T12 / FR-T13）
  // -------------------------------------------------------------------------

  /**
   * 成绩列表（FR-T11）。
   *
   * 分页 `limit` / `offset`，筛选 `paper_id`（只看某套卷）与 `q`（姓名或学号模糊匹配）。
   * **排序由后端定**（`submitted_at` 倒序，同一时刻按 `id` 倒序保证稳定分页），
   * 前端不再排一遍 —— 前端再排一次就会与后端的页边界打架，出现「同一行出现两次」
   * 或「某一行谁也不显示」。
   */
  async listAttempts(query: AttemptListQuery): Promise<AttemptListOut> {
    const params = new URLSearchParams();
    params.set('limit', String(query.limit));
    params.set('offset', String(query.offset));
    if (query.paper_id !== undefined) params.set('paper_id', String(query.paper_id));
    const q = query.q?.trim() ?? '';
    if (q !== '') params.set('q', q);
    const payload = await requestJson<unknown>(`${route('list_attempts')}?${params.toString()}`);
    return normalizeAttemptList(payload, query.limit, query.offset);
  },

  /** 一次练习的逐题明细（FR-T12）：概要 + 每题对错与正确答案。 */
  async getAttempt(attemptId: number): Promise<AttemptDetailOut> {
    const payload = await requestJson<unknown>(route('get_attempt', { attempt_id: attemptId }));
    return normalizeAttemptDetail(payload);
  },

  /**
   * 导出**当前筛选下的全部记录**（FR-T13），返回文件字节交给界面自己触发下载。
   *
   * 只带 `format` / `paper_id` / `q`：**不传 `limit` / `offset`**。后端本来就不套用
   * 分页（`export_attempts` 的签名里根本没有这两个参数），但前端传了就是把「当前页」
   * 当成了「全部」——`AttemptExportQuery` 里根本没有分页字段，这里也不补。
   *
   * 不用「浏览器直接导航到该 URL」的写法：那样后端 500 时老师看到的是一屏 JSON，
   * 而不是一句人话（D65）。取成 `Blob` 才能把失败渲染成提示。
   */
  async exportAttempts(
    query: AttemptExportQuery,
  ): Promise<{ blob: Blob; filename: string; contentType: string }> {
    const params = new URLSearchParams();
    params.set('format', query.format);
    if (query.paper_id !== undefined) params.set('paper_id', String(query.paper_id));
    const q = query.q?.trim() ?? '';
    if (q !== '') params.set('q', q);
    return fetchAttachment(`${route('export_attempts')}?${params.toString()}`, 60_000);
  },
};
