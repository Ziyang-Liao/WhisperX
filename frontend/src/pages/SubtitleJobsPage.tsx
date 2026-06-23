import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listMedia } from "../api/media";
import type { MediaFile, SubtitleTrack } from "../types/media";

/**
 * Subtitle Jobs — a live view of the video subtitling pipeline (driven by
 * /api/media, not the legacy audio batch system). Shows each video's
 * transcription status and the per-language subtitle track progress.
 */
export default function SubtitleJobsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["media", "jobs"],
    queryFn: () => listMedia({ page: 1, page_size: 100 }),
    refetchInterval: 4000, // statuses progress in the background
  });

  const items = data?.items ?? [];

  // A media is "active" while transcription is running or any track is pending/processing.
  const isActive = (m: MediaFile) =>
    m.transcription_status === "pending" ||
    m.transcription_status === "processing" ||
    m.subtitle_tracks.some((t) => t.status === "pending" || t.status === "processing");

  function trackSummary(tracks: SubtitleTrack[]) {
    const done = tracks.filter((t) => t.status === "completed").length;
    return `${done}/${tracks.length}`;
  }

  return (
    <>
      <div className="page-header">
        <h2>字幕任务</h2>
      </div>

      <p style={{ color: "#6b7280", marginBottom: "1rem", fontSize: "0.9rem" }}>
        视频转写与多语言字幕生成的实时进度。任务自动执行，无需手动触发。
      </p>

      {isLoading ? (
        <div className="empty-state">加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty-state" data-testid="jobs-empty">
          暂无任务。在「视频字幕」页上传视频后，转写与翻译会自动开始。
        </div>
      ) : (
        <table data-testid="jobs-table">
          <thead>
            <tr>
              <th>视频</th>
              <th>转写状态</th>
              <th>源语言</th>
              <th>字幕进度</th>
              <th>各语种字幕</th>
            </tr>
          </thead>
          <tbody>
            {items.map((m: MediaFile) => (
              <tr key={m.id} data-testid={`job-row-${m.id}`}>
                <td>
                  <Link to={`/media/${m.id}`}>{m.filename}</Link>
                  {isActive(m) && (
                    <span style={{ marginLeft: 6, fontSize: "0.75rem", color: "#1e40af" }}>
                      ● 处理中
                    </span>
                  )}
                </td>
                <td>
                  <span className={`status-badge status-${m.transcription_status}`}>
                    {m.transcription_status}
                  </span>
                  {m.error_message && (
                    <div style={{ color: "#991b1b", fontSize: "0.75rem" }}>
                      {m.error_message}
                    </div>
                  )}
                </td>
                <td>{m.source_language ?? "-"}</td>
                <td>{m.subtitle_tracks.length ? trackSummary(m.subtitle_tracks) : "-"}</td>
                <td>
                  {m.subtitle_tracks.length === 0 ? (
                    <span style={{ color: "#9ca3af" }}>-</span>
                  ) : (
                    <span style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
                      {m.subtitle_tracks.map((t) => (
                        <span
                          key={t.id}
                          className={`status-badge status-${t.status}`}
                          title={`${t.language}: ${t.status}`}
                        >
                          {t.language}
                          {t.is_source ? "*" : ""}
                        </span>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
