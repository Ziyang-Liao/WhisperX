import client from "./client";
import type {
  TranscriptionTask,
  PaginatedResponse,
} from "../types";

export async function triggerTranscription(
  fileIds?: number[]
): Promise<TranscriptionTask> {
  const body = fileIds ? { file_ids: fileIds } : null;
  const { data } = await client.post<TranscriptionTask>(
    "/transcription/trigger",
    body
  );
  return data;
}

export async function listTasks(params?: {
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<TranscriptionTask>> {
  const { data } = await client.get<PaginatedResponse<TranscriptionTask>>(
    "/transcription/tasks",
    { params }
  );
  return data;
}

export async function getTask(id: number): Promise<TranscriptionTask> {
  const { data } = await client.get<TranscriptionTask>(
    `/transcription/tasks/${id}`
  );
  return data;
}
