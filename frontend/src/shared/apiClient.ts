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
  type ErrorResponse,
  type LayoutIn,
  type PaperConfigIn,
  type PaperDetail,
  type PaperItemOut,
  type PaperListOut,
  type PublishResult,
  type SheetMode,
} from './api';

/** 出题请求体 —— `POST /api/papers`（契约 §5.1：`config`, `seed?`, `title?`）。 */
export interface CreatePaperRequest {
  config: PaperConfigIn;
  seed?: number;
  title?: string;
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

export const teacherApi = {
  /** `POST /api/papers` —— 生成新卷（FR-T1 / FR-T2）。
   *
   * D55（已冻结）：单资源参数**平铺** —— body 就是 `PaperConfigIn` 的字段，
   * `seed` / `title` 走可选查询参数。旧契约措辞写的 `{config, seed, title}` 已被判定为文档缺陷，
   * 所以这里只有一条路径，没有兼容分支。
   */
  async createPaper(body: CreatePaperRequest): Promise<PaperDetail> {
    const { config, seed, title } = body;
    const query = new URLSearchParams();
    if (seed !== undefined) query.set('seed', String(seed));
    if (title !== undefined) query.set('title', title);
    const search = query.toString();
    const url = route('create_paper') + (search ? `?${search}` : '');

    const payload = await requestJson<unknown>(url, {
      method: 'POST',
      body: JSON.stringify(config),
    });
    return parsePaperDetail(payload);
  },

  /** `GET /api/papers` —— 卷库（FR-T14）。 */
  listPapers(): Promise<PaperListOut> {
    return requestJson<PaperListOut>(route('list_papers'));
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
