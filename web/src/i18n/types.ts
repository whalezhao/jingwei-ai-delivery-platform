/** 语言包只放字符串叶子；这些工具类型负责键联合与 en/zh 结构对齐。 */

export type DeepKeys<T> = T extends string
  ? never
  : {
      [K in keyof T & string]: T[K] extends string ? K : `${K}.${DeepKeys<T[K]>}`;
    }[keyof T & string];

/** 递归保留键结构，把中文源的字面量值放宽为 string，供 en 的 satisfies 校验。 */
export type SameShape<T> = {
  [K in keyof T]: T[K] extends string ? string : SameShape<T[K]>;
};