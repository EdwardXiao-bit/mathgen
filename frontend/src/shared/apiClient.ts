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
    papers: raw.papers,
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
};
