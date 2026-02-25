export interface WordSegment {
  word: string;
  start: number;
  end: number;
  score: number;
  speaker: string | null;
}

export interface TranscriptSegment {
  text: string;
  start: number;
  end: number;
  speaker: string | null;
  words: WordSegment[];
}

export interface TranscriptResult {
  segments: TranscriptSegment[];
  language: string;
  duration: number;
}

export interface AudioFile {
  id: number;
  filename: string;
  file_size: number;
  duration: number;
  format: string;
  upload_time: string;
  transcription_status: string;
}

export interface TranscriptionTask {
  id: number;
  trigger_type: string;
  status: string;
  total_files: number;
  processed_files: number;
  success_count: number;
  failure_count: number;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}
