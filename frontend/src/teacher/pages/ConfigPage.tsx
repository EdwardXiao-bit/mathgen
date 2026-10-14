/** `/teacher` —— 出题配置页（FR-T1/T2）：六维表单 → 生成 → 跳转预览改题页。 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, teacherApi } from '../../shared/apiClient';
import { queryKeys } from '../../shared/queryKeys';
import { ConfigForm, DEFAULT_DRAFT, type ConfigDraft } from '../ConfigForm';

function readError(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}：${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}

export function ConfigPage() {
  const [draft, setDraft] = useState<ConfigDraft>(DEFAULT_DRAFT);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const createMutation = useMutation({
    mutationFn: (payload: ConfigDraft) => {
      const seedText = payload.seedText.trim();
      return teacherApi.createPaper({
        config: payload.config,
        ...(seedText === '' ? {} : { seed: Number(seedText) }),
        ...(payload.title.trim() === '' ? {} : { title: payload.title.trim() }),
        // 第六维：把用户在表单里勾的四个开关一并落库（见 createPaper 的两条落库路径）
        answerLayout: payload.answerLayout,
      });
    },
    onSuccess: ({ paper, answerLayoutMode }) => {
      queryClient.setQueryData(queryKeys.paper(paper.id), paper);
      void queryClient.invalidateQueries({ queryKey: queryKeys.papersRoot });
      // 如实告诉老师「答案版形式是怎么落库的」：一次创建带上 / 创建后补一次 PATCH
      if (answerLayoutMode === 'follow-up-patch') {
        window.sessionStorage.setItem(
          `mathgen.answerLayoutNotice.${paper.id}`,
          '答案版形式已在创建后单独保存（后端创建接口暂不接受 answer_layout）。',
        );
      }
      navigate(`/teacher/paper/${paper.id}`);
    },
  });

  return (
    <div className="config-page">
      <section className="page-intro">
        <h1>出题配置</h1>
        <p>
          六个维度：运算类型、数值范围、特殊约束、题型、题量与答案版形式。生成后可预览、改题、调排版，
          再打印成 A4 卷子。
        </p>
      </section>
      <ConfigForm
        draft={draft}
        onChange={setDraft}
        onSubmit={() => createMutation.mutate(draft)}
        submitting={createMutation.isPending}
        submitError={createMutation.isError ? readError(createMutation.error) : null}
      />
    </div>
  );
}
