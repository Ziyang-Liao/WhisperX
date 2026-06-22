import axios from "axios";
import client from "./client";
import type {
  CreateMediaResponse,
  MediaFile,
  PaginatedResponse,
  SubtitleTrack,
} from "../types/media";

/**
 * Upload a video via presigned S3 PUT:
 *   1. create the media record (get presigned URL)
 *   2. PUT the bytes straight to S3 (bypasses our API)
 *   3. tell the API the upload is complete (enqueues transcription)
 */
export async function uploadMedia(
  file: File,
  onProgress?: (pct: number) => void
): Promise<MediaFile> {
  const { data: created } = await client.post<CreateMediaResponse>("/media", {
    filename: file.name,
    content_type: file.type || "application/octet-stream",
  });

  // Direct-to-S3 PUT. Use a bare axios (not our /api client) so no auth/baseURL
  // is attached, and the Content-Type matches what was presigned.
  await axios.put(created.upload_url, file, {
    headers: { "Content-Type": file.type || "application/octet-stream" },
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  });

  const { data } = await client.post<MediaFile>(`/media/${created.media_id}/complete`);
  return data;
}

export async function listMedia(params: {
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<MediaFile>> {
  const { data } = await client.get<PaginatedResponse<MediaFile>>("/media", { params });
  return data;
}

export async function getMedia(id: number): Promise<MediaFile> {
  const { data } = await client.get<MediaFile>(`/media/${id}`);
  return data;
}

export async function getMediaStreamUrl(id: number): Promise<string> {
  const { data } = await client.get<{ url: string }>(`/media/${id}/stream`);
  return data.url;
}

export async function deleteMedia(id: number): Promise<void> {
  await client.delete(`/media/${id}`);
}

export async function generateSubtitles(
  id: number,
  targetLanguages: string[]
): Promise<SubtitleTrack[]> {
  const { data } = await client.post<SubtitleTrack[]>(`/media/${id}/subtitles`, {
    target_languages: targetLanguages,
  });
  return data;
}

export async function listSubtitles(id: number): Promise<SubtitleTrack[]> {
  const { data } = await client.get<SubtitleTrack[]>(`/media/${id}/subtitles`);
  return data;
}

export async function getSubtitleUrl(
  id: number,
  language: string,
  fmt: "srt" | "vtt"
): Promise<string> {
  const { data } = await client.get<{ url: string }>(
    `/media/${id}/subtitles/${language}/${fmt}`
  );
  return data.url;
}
