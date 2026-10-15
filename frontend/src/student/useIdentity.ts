/**
 * 学生身份的 React 绑定：读 / 报到 / 忘记。
 *
 * 报到（`create_student`）只在**首次**发生；之后进页面直接用已记住的身份，
 * 不再打一次接口——学生每次做题都多一次网络往返没有必要，弱网下还容易失败。
 */
import { useCallback, useState } from 'react';

import { registerStudent } from './api';
import { clearIdentity, readIdentity, saveIdentity, type StudentIdentity } from './identity';

export interface IdentityApi {
  identity: StudentIdentity | null;
  register: (name: string, studentNo: string) => Promise<StudentIdentity>;
  forget: () => void;
}

export function useIdentity(): IdentityApi {
  const [identity, setIdentity] = useState<StudentIdentity | null>(() => readIdentity());

  const register = useCallback(async (name: string, studentNo: string) => {
    const trimmedName = name.trim();
    const trimmedNo = studentNo.trim();
    const studentId = await registerStudent(trimmedName, trimmedNo);
    const next: StudentIdentity = {
      student_id: studentId,
      name: trimmedName,
      student_no: trimmedNo,
    };
    saveIdentity(next);
    setIdentity(next);
    return next;
  }, []);

  const forget = useCallback(() => {
    clearIdentity();
    setIdentity(null);
  }, []);

  return { identity, register, forget };
}
