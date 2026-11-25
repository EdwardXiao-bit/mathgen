/**
 * 模式 C 上传页（M18-05）：选一份卷、填学生、把卷面照片传上来，建一个识别批次。
 *
 * 页面只负责**收图与建批次**：校正与识别在后端完成（M18-03 / M18-04），
 * 上传成功后跳到校对页看识别结果。
 *
 * 两条界面约束：
 *   ① 一次最多两页（后端 `MAX_PAGES`），选多了要当场说清，不等提交后才报错；
 *   ② 这一页在**老师电脑**上使用。照片是老师用手机拍好、传到电脑上再选的，
 *      所以入口只有「选图片」，没有调用本机摄像头的「拍一页」。
 */

import { useEffect, useMemo, useState } from 'react';
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
import { ErrorState, Input } from '../../ui';

import './OcrUploadPage.css';

const BYTES_PER_MB = 1024 * 1024;

/** 一期只做手机拍照上传（D90）；扫描件与图片走同一个文件入口。 */
const SOURCE: OcrSource = 'phone';

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

  const papersQuery = useQuery({
    queryKey: queryKeys.papers({ limit: 100, offset: 0, status: 'published' }),
    queryFn: () => teacherApi.listPapers({ limit: 100, offset: 0, status: 'published' }),
  });

  const [paperId, setPaperId] = useState<string>('');
  const [studentName, setStudentName] = useState('');
  const [studentNo, setStudentNo] = useState('');
  const [picked, setPicked] = useState<PickedPage[]>([]);
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
    if (picked.length === 0) errors.files = '还没有选图片';
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    mutation.mutate({
      paperId: Number(paperId),
      studentName: studentName.trim(),
      studentNo: studentNo.trim(),
      source: SOURCE,
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
          title="没有读到已发布的卷"
          what={loadError}
          how="确认后端已启动，然后刷新这一页。还没有发布过卷的话，先在出题配置里生成一份并发布。"
          actions={
            <button className="btn" type="button" onClick={() => void papersQuery.refetch()}>
              重试
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="ocr-upload-page">
      <section className="page-intro">
        <h1>拍照批改</h1>
        <p>
          选一份已发布的卷，上传学生写好的卷面照片。识别出每题答案后，你逐题核对，确认完计分。
          一次一份卷，最多 {OCR_MAX_PAGES} 页。
        </p>
      </section>

      {papers.length === 0 ? (
        <div className="dim-card" data-testid="ocr-upload-no-papers">
          <p>还没有已发布的卷。先有一份已发布的卷，才能批改。</p>
          <p>
            <Link className="btn btn-primary" to="/teacher">
              去出题配置
            </Link>
          </p>
        </div>
      ) : (
        <form
          className="ocr-form"
          data-testid="ocr-upload-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <section className="dim-card">
            <h2>考卷与学生</h2>
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
                {chosenPaper ? <span className="ocr-field-hint">卷号 {chosenPaper.code}</span> : null}
              </label>
            </div>

            <div className="ocr-row">
              <Input
                label="学生姓名"
                data-testid="ocr-student-name"
                value={studentName}
                placeholder="例如：肖铭皓"
                invalidReason={fieldErrors.name ?? null}
                onChange={(event) => {
                  setStudentName(event.target.value);
                  setFieldErrors((prev) => ({ ...prev, name: undefined }));
                }}
              />
              <Input
                label="学号"
                data-testid="ocr-student-no"
                value={studentNo}
                placeholder="例如：2024141460455"
                invalidReason={fieldErrors.no ?? null}
                onChange={(event) => {
                  setStudentNo(event.target.value);
                  setFieldErrors((prev) => ({ ...prev, no: undefined }));
                }}
              />
            </div>
          </section>

          <section className="dim-card">
            <h2>卷面照片</h2>
            <p className="ocr-hint">
              用手机拍好，传到这台电脑上，再在这里选。把卷子四个角都拍进画面，光线尽量均匀；
              拍歪了也能自动摆正。
            </p>
            <label className="ocr-picker" data-testid="ocr-file-picker">
              <input
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
              <span className="ocr-picker-title">选择图片</span>
              <span className="ocr-picker-hint">
                JPEG / PNG / WebP，单页不超过 20MB，最多 {OCR_MAX_PAGES} 页
              </span>
            </label>

            {picked.length > 0 ? (
              <ol className="ocr-picked" data-testid="ocr-picked-list">
                {picked.map((page, index) => (
                  <li
                    className="ocr-picked-item"
                    key={`${page.file.name}-${index}`}
                    data-testid={`ocr-picked-${index}`}
                  >
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
                还没有选图片。
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

          <div className="ocr-actions">
            <button
              className="btn btn-primary"
              type="submit"
              data-testid="ocr-upload-submit"
              disabled={mutation.isPending}
            >
              {mutation.isPending ? '上传中…' : '开始识别'}
            </button>
            <Link className="btn" to="/teacher">
              返回出题配置
            </Link>
          </div>
        </form>
      )}
    </div>
  );
}
