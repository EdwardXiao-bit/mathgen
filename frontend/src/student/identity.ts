/**
 * 学生身份的本地记忆（姓名 + 学号，D41 两者必填）。
 *
 * 为什么要记住：一个班共用一台平板时，学生每次做题都重新输一遍姓名学号不可接受；
 * 但**必须允许改**（下一位同学要能覆盖上一位）——所以有 `clearIdentity`，
 * 且界面入口显式提供「不是本人」的按钮，而不是把身份藏起来。
 *
 * 用 `localStorage` 而不是内存：学生可能中途刷新页面、或从二维码重新打开。
 * 微信等内置浏览器里 `localStorage` 可能被禁用，因此读写都要包在 try 里，
 * 失败时退化成「每次重新填」而不是白屏。
 */

export interface StudentIdentity {
  student_id: number;
  name: string;
  student_no: string;
}

const STORAGE_KEY = 'mathgen.student.identity';

export function readIdentity(): StudentIdentity | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StudentIdentity>;
    if (
      typeof parsed.student_id !== 'number' ||
      typeof parsed.name !== 'string' ||
      typeof parsed.student_no !== 'string' ||
      parsed.name === ''
    ) {
      return null;
    }
    return { student_id: parsed.student_id, name: parsed.name, student_no: parsed.student_no };
  } catch {
    return null;
  }
}

export function saveIdentity(value: StudentIdentity): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 隐私模式下写不进去：不影响本次做题，只是下次要重填
  }
}

export function clearIdentity(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // 同上：清不掉也不该打断学生
  }
}
