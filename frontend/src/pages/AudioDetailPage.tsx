import { useRef, useCallback, type ChangeEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getAudio, getTranscript, replaceAudio } from "../api/audio";

const SUPPORTED_FORMATS = [".wav", ".mp3", ".flac", ".m4a", ".ogg"];

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(sec: number): string {
  return `${sec.toFixed(2)}s`;
}

export default function AudioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const audioId = Number(id);
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: audio, isLoading } = useQuery({
    queryKey: ["audio", audioId],
    queryFn: () => getAudio(audioId),
    enabled: !isNaN(audioId),
  });

  const { data: transcript } = useQuery({
    queryKey: ["transcript", audioId],
    queryFn: () => getTranscript(audioId),
    enabled: audio?.transcription_status === "completed",
  });

  const replaceMutation = useMutation({
    mutationFn: (file: File) => replaceAudio(audioId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audio", audioId] });
      queryClient.invalidateQueries({ queryKey: ["transcript", audioId] });
    },
  });

  const handleReplace = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      if (!SUPPORTED_FORMATS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
        alert(`不支持的格式。支持: ${SUPPORTED_FORMATS.join(", ")}`);
        return;
      }
      replaceMutation.mutate(file);
      e.target.value = "";
    },
    [replaceMutation]
  );

  const seekTo = useCallback((time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time;
      audioRef.current.play();
    }
  }, []);

  if (isLoading) return <div className="empty-state">加载中...</div>;
  if (!audio) return <div className="empty-state">音频文件不存在</div>;

  return (
    <>
      <div className="page-header">
        <h2>
          <Link to="/" style={{ color: "#6b7280" }}>
            音频文件
          </Link>{" "}
          / {audio.filename}
        </h2>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            ref={fileInputRef}
            type="file"
            accept={SUPPORTED_FORMATS.join(",")}
            style={{ display: "none" }}
            onChange={handleReplace}
            aria-label="替换音频文件"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={
              replaceMutation.isPending ||
              audio.transcription_status === "processing"
            }
          >
            {replaceMutation.isPending ? "替换中..." : "替换文件"}
          </button>
        </div>
      </div>

      {replaceMutation.isError && (
        <div className="error-message">{replaceMutation.error.message}</div>
      )}

      <div className="detail-card">
        <dl className="detail-grid">
          <div>
            <dt>文件名</dt>
            <dd>{audio.filename}</dd>
          </div>
          <div>
            <dt>时长</dt>
            <dd>{formatDuration(audio.duration)}</dd>
          </div>
          <div>
            <dt>格式</dt>
            <dd>{audio.format}</dd>
          </div>
          <div>
            <dt>大小</dt>
            <dd>{formatSize(audio.file_size)}</dd>
          </div>
          <div>
            <dt>上传时间</dt>
            <dd>{new Date(audio.upload_time).toLocaleString()}</dd>
          </div>
          <div>
            <dt>转录状态</dt>
            <dd>
              <span
                className={`status-badge status-${audio.transcription_status}`}
              >
                {audio.transcription_status}
              </span>
            </dd>
          </div>
        </dl>
      </div>

      <audio
        ref={audioRef}
        controls
        className="audio-player"
        src={`/api/audio/${audioId}/file`}
        aria-label={`播放 ${audio.filename}`}
      />

      {audio.transcription_status === "completed" && transcript ? (
        <div className="transcript-container">
          <h3 style={{ marginBottom: "1rem" }}>转录结果</h3>
          {transcript.segments.map((seg, i) => (
            <div
              key={i}
              className="transcript-segment"
              onClick={() => seekTo(seg.start)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && seekTo(seg.start)}
              aria-label={`跳转到 ${formatTime(seg.start)}`}
            >
              <span className="segment-time">
                [{formatTime(seg.start)}-{formatTime(seg.end)}]
              </span>
              {seg.speaker && (
                <span className="segment-speaker">[{seg.speaker}]</span>
              )}
              <span>{seg.text}</span>
            </div>
          ))}
        </div>
      ) : audio.transcription_status === "pending" ? (
        <div className="empty-state">待转录</div>
      ) : audio.transcription_status === "processing" ? (
        <div className="empty-state">转录中...</div>
      ) : audio.transcription_status === "failed" ? (
        <div className="empty-state">转录失败</div>
      ) : null}
    </>
  );
}
