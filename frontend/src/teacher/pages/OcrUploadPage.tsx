/**
 * 模式 C 上传页（M18-05）：把学生写好的卷子拍下来 / 上传扫描件，建一个识别批次。
 *
 * 这一页只负责**收图与建批次**：校正与会话识别都在后端完成（M18-03 / M18-04），
 * 上传成功后跳到校对页轮询识别进度。
 *
 * 两条界面约束：
 *   ① 一次最多两页（后端 `MAX_PAGES`），选多了要当场说清，不能等提交后才报错；
 *   ② 手机的「拍照」与电脑的「选文件 / 拖入」是**两个入口**（Q20），不要只做一个。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';

import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import {
  OCR_MAX_PAGE_BYTES,
  OCR_MAX_PAGES,
  type OcrSource,
  type OcrUploadInput,
  type PaperSummary,
} from '../../shared/api';
import { teacherApi, uploadOcrBatch } from '../../shared/apiClient';
import { ErrorState, SegmentedControl } from '../../ui';

import './OcrUploadPage.css';

const BYTES_PER_MB = 1024 * 1024;

function formatMb(bytes: number): string {
  return `${(bytes / BYTES_PER_MB).toFixed(1)} MB`;
}

/** 选中的文件在页面上只用来预览与移除，真正上传时才读内容。 */
interface PickedPage {
  file: File;
  /** `URL.createObjectURL` 的地址；卸载或移除时要 revoke，否则整页图片会一直占内存。 */
  preview: string;
}

export function OcrUploadPage() {
  const navigate = useNavigate();
  const cameraRef = useRef<HTMLInputElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const papersQuery = useQuery({
    queryKey: queryKeys.papers({ limit: 100, offset: 0, status: 'published' }),
    queryFn: () => teacherApi.listPapers({ limit: 100, offset: 0, status: 'published' }),
  });

  const [paperId, setPaperId] = useState<string>('');
  const [studentName, setStudentName] = useState('');
  const [studentNo, setStudentNo] = useState('');
  const [source, setSource] = useState<OcrSource>('phone');
  const [picked, setPicked] = useState<PickedPage[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{
    paper?: string;
    name?: string;
    no?: string;
    files?: string;
  }>({});

  // 预览地址随 `picked` 变化重新回收：旧地址不 revoke 会泄漏（一批 2 张 × 十几 MB）。
  useEffect(() => {
    const urls = picked.map((page) => page.preview);
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [picked]);

  const papers: PaperSummary[] = papersQuery.data?.data.papers ?? [];
  const loadError = papersQuery.isError ? readError(papersQuery.error) : null;

  const mutation = useMutation({
    mutationFn: (input: OcrUploadInput) => uploadOcrBatch(input),
    onSuccess: (detail) => {
      navigate(`/teacher/ocr/${detail.batch.id}`);
    },
  });

  const chosenPaper = useMemo(
    () => papers.find((paper) => String(paper.id) === paperId) ?? null,
    [papers, paperId],
  );

  function addFiles(incoming: FileList | null) {
    if (!incoming || incoming.length === 0) return;
    const next = [...picked];
    const problems: string[] = [];
    for (const file of Array.from(incoming)) {
      if (next.length >= OCR_MAX_PAGES) {
        problems.push(`一次最多 ${OCR_MAX_PAGES} 页`);
        break;
      }
      if (!file.type.startsWith('image/')) {
        problems.push(`「${file.name}」不是图片`);
        continue;
      }
      if (file.size > OCR_MAX_PAGE_BYTES) {
        problems.push(`「${file.name}」有 ${formatMb(file.size)}，单页最多 20MB`);
        continue;
      }
      next.push({ file, preview: URL.createObjectURL(file) });
    }
    setPicked(next);
    setFieldErrors((prev) => ({ ...prev, files: problems.length > 0 ? problems.join('；') : undefined }));
  }

  function removePage(index: number) {
    setPicked((prev) => {
      const removed = prev[index];
      if (removed) URL.revokeObjectURL(removed.preview);
      return prev.filter((_, i) => i !== index);
    });
    setFieldErrors((prev) => ({ ...prev, files: undefined }));
  }

  function submit() {
    const errors: typeof fieldErrors = {};
    if (paperId === '') errors.paper = '先选一份已发布的卷';
    if (studentName.trim() === '') errors.name = '写上学生姓名';
    if (studentNo.trim() === '') errors.no = '写上学生学号';
    if (picked.length === 0) errors.files = '至少一页卷面照片';
    setFieldErrors(errors);
    setFormError(null);
    if (Object.keys(errors).length > 0) return;

    mutation.mutate({
      paperId: Number(paperId),
      studentName: studentName.trim(),
      studentNo: studentNo.trim(),
      source,
      files: picked.map((page) => page.file),
    });
  }

  if (papersQuery.isLoading) {
    return (
      <div className="ocr-upload-page">
        <div className="loading-card" data-testid="ocr-upload-loading">
          正在读取已发布的卷…
        </div>
      </div>
    );
  }

  if (loadError !== null) {
    return (
      <div className="ocr-upload-page">
        <ErrorState
          testId="ocr-upload-load-error"
          title="读取不到已发布的卷"
          what={loadError}
          how="确认后端已启动，再刷新这一页。若确实还没有发布过卷，先回出题配置生成并发布一份。"
          actions={
            <button className="btn" type="button" onClick={() => void papersQuery.refetch()}>
              重试
            </button>
          }
        />
      </div>
    );
  }

  if (papers.length === 0) {
    return (
      <div className="ocr-upload-page">
        <section className="page-intro">
          <h1>拍照批改</h1>
          <p>把学生写好的卷子拍下来，识别每题答案，核对后自动出分。</p>
        </section>
        <div className="ocr-empty" data-testid="ocr-upload-no-papers">
          <p>现在还没有已发布的卷，先有一份卷才能批改。</p>
          <Link className="btn btn-primary" to="/teacher">
            回出题配置
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="ocr-upload-page">
      <section className="page-intro">
        <h1>拍照批改</h1>
        <p>
          把学生写完的卷子拍下来，或者上传扫描件；识别出每题答案后，你逐题核对，确认完自动出分。
          一次最多 {OCR_MAX_PAGES} 页。
        </p>
      </section>

      <form
        className="ocr-form"
        data-testid="ocr-upload-form"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <section className="ocr-block">
          <h2 className="ocr-block-title">这次批改哪份卷</h2>
          <div className="ocr-row">
            <label className="ocr-field">
              <span className="ocr-field-label">卷</span>
              <select
                data-testid="ocr-paper-select"
                value={paperId}
                onChange={(event) => {
                  setPaperId(event.target.value);
                  setFieldErrors((prev) => ({ ...prev, paper: undefined }));
                }}
              >
                <option value="">请选择</option>
                {papers.map((paper) => (
                  <option key={paper.id} value={String(paper.id)}>
                    {paper.title}（{paper.code}）
                  </option>
                ))}
              </select>
              {fieldErrors.paper ? (
                <span className="ocr-field-reason" role="alert">
                  {fieldErrors.paper}
                </span>
              ) : null}
              {chosenPaper ? (
                <span className="ocr-field-hint">卷号 {chosenPaper.code}</span>
              ) : null}
            </label>
          </div>

          <div className="ocr-row">
            <label className="ocr-field">
              <span className="ocr-field-label">学生姓名</span>
              <input
                className="mg-input"
                type="text"
                data-testid="ocr-student-name"
                value={studentName}
                placeholder="例如：肖铭皓"
                onChange={(event) => {
                  setStudentName(event.target.value);
                  setFieldErrors((prev) => ({ ...prev, name: undefined }));
                }}
              />
              {fieldErrors.name ? (
                <span className="ocr-field-reason" role="alert">
                  {fieldErrors.name}
                </span>
              ) : null}
            </label>
            <label className="ocr-field">
              <span className="ocr-field-label">学号</span>
              <input
                className="mg-input"
                type="text"
                data-testid="ocr-student-no"
                value={studentNo}
                placeholder="例如：2024141460455"
                onChange={(event) => {
                  setStudentNo(event.target.value);
                  setFieldErrors((prev) => ({ ...prev, no: undefined }));
                }}
              />
              {fieldErrors.no ? (
                <span className="ocr-field-reason" role="alert">
                  {fieldErrors.no}
                </span>
              ) : null}
            </label>
          </div>
        </section>

        <section className="ocr-block">
          <h2 className="ocr-block-title">怎么拍的</h2>
          <SegmentedControl<OcrSource>
            name="ocr-source"
            ariaLabel="手机拍照或扫描件"
            testId="ocr-source"
            value={source}
            onChange={setSource}
            options={[
              { value: 'phone', label: '手机拍照' },
              { value: 'scan', label: '扫描件或图片' },
            ]}
          />
          <p className="ocr-hint">
            拍的时候把卷子四个角都拍进画面，光线尽量均匀；歪一点没关系，系统会自动摆正。
          </p>
        </section>

        <section className="ocr-block">
          <h2 className="ocr-block-title">卷面</h2>
          <div className="ocr-pickers">
            <label className="ocr-picker" data-testid="ocr-camera-picker">
              <input
                ref={cameraRef}
                type="file"
                accept="image/*"
                capture="environment"
                hidden
                data-testid="ocr-camera-input"
                onChange={(event) => {
                  addFiles(event.target.files);
                  event.target.value = '';
                }}
              />
              <span className="ocr-picker-title">拍一页</span>
              <span className="ocr-picker-hint">用手机后置相机</span>
            </label>
            <label className="ocr-picker" data-testid="ocr-file-picker">
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                hidden
                data-testid="ocr-file-input"
                onChange={(event) => {
                  addFiles(event.target.files);
                  event.target.value = '';
                }}
              />
              <span className="ocr-picker-title">选图片</span>
              <span className="ocr-picker-hint">JPEG / PNG / WebP，最多 {OCR_MAX_PAGES} 页</span>
            </label>
          </div>

          {picked.length > 0 ? (
            <ol className="ocr-picked" data-testid="ocr-picked-list">
              {picked.map((page, index) => (
                <li className="ocr-picked-item" key={`${page.file.name}-${index}`} data-testid={`ocr-picked-${index}`}>
                  <img className="ocr-picked-thumb" src={page.preview} alt={`第 ${index + 1} 页预览`} />
                  <div className="ocr-picked-meta">
                    <span className="ocr-picked-name">第 {index + 1} 页</span>
                    <span className="ocr-picked-size">{formatMb(page.file.size)}</span>
                  </div>
                  <button
                    className="btn ocr-picked-remove"
                    type="button"
                    data-testid={`ocr-picked-remove-${index}`}
                    onClick={() => removePage(index)}
                  >
                    移除
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="ocr-hint" data-testid="ocr-picked-empty">
              还没选图。拍完或选完会在这里看到两页缩略图。
            </p>
          )}

          {fieldErrors.files ? (
            <p className="ocr-field-reason" role="alert" data-testid="ocr-files-error">
              {fieldErrors.files}
            </p>
          ) : null}
        </section>

        {mutation.isError ? (
          <p className="form-error" role="alert" data-testid="ocr-upload-error">
            {readError(mutation.error)}
          </p>
        ) : null}
        {formError ? (
          <p className="form-error" role="alert">
            {formError}
          </p>
        ) : null}

        <div className="ocr-actions">
          <button
            className="btn btn-primary"
            type="submit"
            data-testid="ocr-upload-submit"
            disabled={mutation.isPending}
          >
            {mutation.isPending ? '上传中…' : '上传并识别'}
          </button>
          <Link className="btn" to="/teacher">
            先不传
          </Link>
        </div>
      </form>
    </div>
  );
}
