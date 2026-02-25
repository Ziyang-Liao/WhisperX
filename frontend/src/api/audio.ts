import client from "./client";
import type {
  AudioFile,
  PaginatedResponse,
  TranscriptResult,
} from "../types";

export async function uploadAudio(files: File[]): Promise<AudioFile[]> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  const { data } = await client.post<AudioFile[]>("/audio/upload", form);
  return data;
}

export async function getAudio(id: number): Promise<AudioFile> {
  const { data } = await client.get<AudioFile>(`/audio/${id}`);
  return data;
}

export async function listAudio(params: {
  page?: number;
  page_size?: number;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}): Promise<PaginatedResponse<AudioFile>> {
  const { data } = await client.get<PaginatedResponse<AudioFile>>("/audio", {
    params,
  });
  return data;
}

export async function replaceAudio(
  id: number,
  file: File
): Promise<AudioFile> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await client.put<AudioFile>(`/audio/${id}`, form);
  return data;
}

export async function deleteAudio(id: number): Promise<void> {
  await client.delete(`/audio/${id}`);
}

export async function getTranscript(id: number): Promise<TranscriptResult> {
  const { data } = await client.get<TranscriptResult>(
    `/audio/${id}/transcript`
  );
  return data;
}
