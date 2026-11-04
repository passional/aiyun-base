/**
 * 顺序执行器：批量转写按文件逐个执行，单个文件失败不阻断后续文件。
 *
 * 与 UI 解耦：调用方传入「单文件转写」回调，并可在回调里自行更新行状态。
 * 返回逐项结果，索引与输入顺序一致，便于按行回填。
 */

export interface SequentialBatchItemResult<TResult> {
  index: number;
  status: "success" | "failed";
  value?: TResult;
  error?: unknown;
}

export interface SequentialBatchOptions<TItem, TResult> {
  onItemStart?: (item: TItem, index: number) => void;
  onItemSuccess?: (item: TItem, result: TResult, index: number) => void;
  onItemFailure?: (item: TItem, error: unknown, index: number) => void;
}

export async function runSequential<TItem, TResult>(
  items: readonly TItem[],
  transcribe: (item: TItem, index: number) => Promise<TResult>,
  options: SequentialBatchOptions<TItem, TResult> = {}
): Promise<SequentialBatchItemResult<TResult>[]> {
  const results: SequentialBatchItemResult<TResult>[] = [];

  for (const [index, item] of items.entries()) {
    options.onItemStart?.(item, index);
    try {
      const value = await transcribe(item, index);
      results.push({ index, status: "success", value });
      options.onItemSuccess?.(item, value, index);
    } catch (error) {
      results.push({ index, status: "failed", error });
      options.onItemFailure?.(item, error, index);
    }
  }

  return results;
}
