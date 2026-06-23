import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getMedia,
  getMediaStreamUrl,
  getSubtitleUrl,
  deleteMedia,
} from "../api/media";

export default function MediaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const mediaId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [trackUrls, setTrackUrls] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => deleteMedia(mediaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media"] });
      navigate("/");
    },
  });

  const { data: media, isLoading } = useQuery({
    queryKey: ["media", mediaId],
    queryFn: () => getMedia(mediaId),
    enabled: !isNaN(mediaId),
    refetchInterval: (q) =>
      // keep polling while anything is still in flight
      q.state.data &&
      (q.state.data.transcription_status === "completed" &&
        q.state.data.subtitle_tracks.every((t) => t.status === "completed" || t.status === "failed"))
        ? false
        : 4000,
  });

  // Resolve a presigned stream URL for the video player.
  useEffect(() => {
    if (media && media.transcription_status === "completed") {
      getMediaStreamUrl(mediaId).then(setVideoUrl).catch(() => setVideoUrl(""));
    }
  }, [media, mediaId]);

  // Resolve presigned VTT URLs for the completed tracks so the player can load them.
  useEffect(() => {
    if (!media) return;
    const done = media.subtitle_tracks.filter((t) => t.status === "completed");
    Promise.all(
      done.map((t) =>
        getSubtitleUrl(mediaId, t.language, "vtt").then((url) => [t.language, url] as const)
      )
    ).then((pairs) => setTrackUrls(Object.fromEntries(pairs)));
  }, [media, mediaId]);

  async function download(language: string, fmt: "srt" | "vtt") {
    const url = await getSubtitleUrl(mediaId, language, fmt);
    window.open(url, "_blank");
  }

  if (isLoading) return <div className="empty-state">加载中...</div>;
  if (!media) return <div className="empty-state">视频不存在</div>;

  const isVideo = media.media_type === "video";

  return (
    <>
      <div className="page-header">
        <h2>
          <Link to="/" style={{ color: "#6b7280" }}>
            视频字幕
          </Link>{" "}
          / {media.filename}
        </h2>
        <button
          className="btn-danger"
          onClick={() => setConfirmDelete(true)}
          data-testid="detail-delete"
        >
          删除
        </button>
      </div>

      <div className="detail-card">
        <dl className="detail-grid">
          <div>
            <dt>类型</dt>
            <dd>{media.media_type}</dd>
          </div>
          <div>
            <dt>格式</dt>
            <dd>{media.format}</dd>
          </div>
          <div>
            <dt>源语言</dt>
            <dd>{media.source_language ?? "-"}</dd>
          </div>
          <div>
            <dt>转写状态</dt>
            <dd>
              <span className={`status-badge status-${media.transcription_status}`}>
                {media.transcription_status}
              </span>
            </dd>
          </div>
        </dl>
        {media.error_message && (
          <div className="error-message">{media.error_message}</div>
        )}
      </div>

      {videoUrl && isVideo && (
        <video
          controls
          className="audio-player"
          src={videoUrl}
          crossOrigin="anonymous"
          data-testid="video-player"
        >
          {media.subtitle_tracks
            .filter((t) => trackUrls[t.language])
            .map((t) => (
              <track
                key={t.id}
                kind="subtitles"
                srcLang={t.language}
                label={t.language}
                src={trackUrls[t.language]}
                default={t.is_source}
              />
            ))}
        </video>
      )}
      {videoUrl && !isVideo && (
        <audio controls className="audio-player" src={videoUrl} data-testid="audio-player" />
      )}

      <div className="detail-card">
        <h3 style={{ marginBottom: "1rem" }}>字幕轨</h3>
        {media.subtitle_tracks.length === 0 ? (
          <div className="empty-state" data-testid="no-tracks">
            尚无字幕。在列表页选择语种并点「生成字幕」。
          </div>
        ) : (
          <table data-testid="tracks-table">
            <thead>
              <tr>
                <th>语种</th>
                <th>类型</th>
                <th>状态</th>
                <th>下载</th>
              </tr>
            </thead>
            <tbody>
              {media.subtitle_tracks.map((t) => (
                <tr key={t.id} data-testid={`track-${t.language}`}>
                  <td>{t.language}</td>
                  <td>{t.is_source ? "源语言" : "翻译"}</td>
                  <td>
                    <span className={`status-badge status-${t.status}`}>{t.status}</span>
                    {t.error_message && (
                      <div style={{ color: "#991b1b", fontSize: "0.75rem" }}>
                        {t.error_message}
                      </div>
                    )}
                  </td>
                  <td style={{ display: "flex", gap: "0.5rem" }}>
                    <button
                      disabled={t.status !== "completed"}
                      onClick={() => download(t.language, "srt")}
                      data-testid={`dl-srt-${t.language}`}
                    >
                      SRT
                    </button>
                    <button
                      disabled={t.status !== "completed"}
                      onClick={() => download(t.language, "vtt")}
                      data-testid={`dl-vtt-${t.language}`}
                    >
                      VTT
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {confirmDelete && (
        <div
          className="confirm-dialog-overlay"
          onClick={() => setConfirmDelete(false)}
          role="dialog"
          aria-modal="true"
          aria-label="确认删除"
        >
          <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>确认删除</h3>
            <p>
              确定要删除「{media.filename}」吗？这会同时删除该视频及其所有语言的字幕，
              <strong>此操作不可撤销</strong>。
            </p>
            <div className="confirm-dialog-actions">
              <button onClick={() => setConfirmDelete(false)}>取消</button>
              <button
                className="btn-danger"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                data-testid="confirm-delete"
              >
                {deleteMutation.isPending ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
