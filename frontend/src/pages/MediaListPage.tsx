import { useState, useRef, useCallback, type ChangeEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  uploadMedia,
  listMedia,
  deleteMedia,
  generateSubtitles,
} from "../api/media";
import { LANGUAGE_OPTIONS, type MediaFile } from "../types/media";

const VIDEO_ACCEPT = "video/*,audio/*";
const PAGE_SIZE = 20;

function formatDuration(seconds: number): string {
  if (!seconds) return "-";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MediaListPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [selectedLangs, setSelectedLangs] = useState<Set<string>>(new Set(["en"]));
  // Media pending deletion (shows the confirm dialog). null = no dialog.
  const [pendingDelete, setPendingDelete] = useState<MediaFile | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["media", page],
    queryFn: () => listMedia({ page, page_size: PAGE_SIZE }),
    refetchInterval: 5000, // statuses progress in the background
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadMedia(file, setUploadPct),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media"] });
      setError("");
      setUploadPct(null);
    },
    onError: (err: Error) => {
      setError(err.message);
      setUploadPct(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMedia,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["media"] });
      setPendingDelete(null);
      setError("");
    },
    onError: (err: Error) => {
      setPendingDelete(null);
      setError(err.message);
    },
  });

  const genMutation = useMutation({
    mutationFn: (id: number) => generateSubtitles(id, Array.from(selectedLangs)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["media"] }),
    onError: (err: Error) => setError(err.message),
  });

  const handleUpload = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadMutation.mutate(file);
      e.target.value = "";
    },
    [uploadMutation]
  );

  const toggleLang = useCallback((code: string) => {
    setSelectedLangs((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  const items = data?.items ?? [];
  const totalPages = data?.total_pages ?? 1;

  return (
    <>
      <div className="page-header">
        <h2>视频字幕</h2>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <input
            ref={fileInputRef}
            type="file"
            accept={VIDEO_ACCEPT}
            style={{ display: "none" }}
            onChange={handleUpload}
            aria-label="上传视频"
            data-testid="file-input"
          />
          <button
            className="btn-primary"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            data-testid="upload-btn"
          >
            {uploadMutation.isPending
              ? `上传中 ${uploadPct ?? 0}%`
              : "上传视频"}
          </button>
        </div>
      </div>

      {error && (
        <div className="error-message" data-testid="error">
          {error}
        </div>
      )}

      <div className="toolbar" data-testid="lang-select">
        <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>目标语种(多选):</span>
        {LANGUAGE_OPTIONS.map((opt) => (
          <label key={opt.code} style={{ fontSize: "0.85rem", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={selectedLangs.has(opt.code)}
              onChange={() => toggleLang(opt.code)}
              data-testid={`lang-${opt.code}`}
            />{" "}
            {opt.label}
          </label>
        ))}
      </div>

      {isLoading ? (
        <div className="empty-state">加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty-state" data-testid="empty">
          暂无视频,点击「上传视频」开始
        </div>
      ) : (
        <>
          <table data-testid="media-table">
            <thead>
              <tr>
                <th>文件名</th>
                <th>时长</th>
                <th>大小</th>
                <th>源语言</th>
                <th>转写状态</th>
                <th>字幕语种</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m: MediaFile) => (
                <tr key={m.id} data-testid={`media-row-${m.id}`}>
                  <td>
                    <Link to={`/media/${m.id}`}>{m.filename}</Link>
                  </td>
                  <td>{formatDuration(m.duration)}</td>
                  <td>{formatSize(m.file_size)}</td>
                  <td>{m.source_language ?? "-"}</td>
                  <td>
                    <span className={`status-badge status-${m.transcription_status}`}>
                      {m.transcription_status}
                    </span>
                  </td>
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
                  <td style={{ display: "flex", gap: "0.4rem" }}>
                    <button
                      onClick={() => genMutation.mutate(m.id)}
                      disabled={
                        m.transcription_status !== "completed" ||
                        selectedLangs.size === 0 ||
                        genMutation.isPending
                      }
                      title={
                        m.transcription_status !== "completed"
                          ? "等待转写完成后可生成翻译字幕"
                          : "为选中语种生成字幕"
                      }
                      data-testid={`gen-${m.id}`}
                    >
                      生成字幕
                    </button>
                    <button
                      className="btn-danger"
                      onClick={() => setPendingDelete(m)}
                      data-testid={`del-${m.id}`}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
              下一页
            </button>
          </div>
        </>
      )}

      {pendingDelete && (
        <div
          className="confirm-dialog-overlay"
          onClick={() => setPendingDelete(null)}
          role="dialog"
          aria-modal="true"
          aria-label="确认删除"
        >
          <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>确认删除</h3>
            <p>
              确定要删除「{pendingDelete.filename}」吗？这会同时删除该视频及其所有语言的字幕，
              <strong>此操作不可撤销</strong>。
            </p>
            <div className="confirm-dialog-actions">
              <button onClick={() => setPendingDelete(null)}>取消</button>
              <button
                className="btn-danger"
                onClick={() => deleteMutation.mutate(pendingDelete.id)}
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
