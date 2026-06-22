export interface SubtitleTrack {
  id: number;
  language: string;
  is_source: boolean;
  status: string;
  error_message: string | null;
}

export interface MediaFile {
  id: number;
  filename: string;
  media_type: string;
  file_size: number;
  duration: number;
  format: string;
  source_language: string | null;
  transcription_status: string;
  error_message: string | null;
  upload_time: string;
  subtitle_tracks: SubtitleTrack[];
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface CreateMediaResponse {
  media_id: number;
  upload_url: string;
  s3_key: string;
}

// Common target languages offered in the multi-select. value = code sent to the
// backend (used both as the SubtitleTrack.language and the translation target).
export const LANGUAGE_OPTIONS: { code: string; label: string }[] = [
  { code: "en", label: "English" },
  { code: "zh", label: "中文" },
  { code: "ja", label: "日本語" },
  { code: "ko", label: "한국어" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" },
  { code: "pt", label: "Português" },
  { code: "ru", label: "Русский" },
  { code: "ar", label: "العربية" },
];
