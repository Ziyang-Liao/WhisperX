import { useState, useCallback, useRef, type ChangeEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listAudio, uploadAudio, deleteAudio } from "../api/audio";
import type { AudioFile } from "../types";

const SUPPORTED_FORMATS = [".wav", ".mp3", ".flac", ".m4a", ".ogg"];
const PAGE_SIZE = 20;

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

export default function AudioListPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [sortBy, setSortBy] = useState("upload_time");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [error, setError] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["audio", page, search, sortBy, sortOrder],
    queryFn: () =>
      listAudio({
        page,
        page_size: PAGE_SIZE,
        search: search || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
  });

  const uploadMutation = useMutation({
    mutationFn: uploadAudio,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audio"] });
      setError("");
    },
    onError: (err: Error) => setError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) await deleteAudio(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audio"] });
      setSelected(new Set());
      setShowDeleteConfirm(false);
      setError("");
    },
    onError: (err: Error) => {
      setShowDeleteConfirm(false);
      setError(err.message);
    },
  });

  const handleUpload = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      const invalid = files.filter(
        (f) => !SUPPORTED_FORMATS.some((ext) => f.name.toLowerCase().endsWith(ext))
      );
      if (invalid.length) {
        setError(
          `不支持的格式: ${invalid.map((f) => f.name).join(", ")}。支持: ${SUPPORTED_FORMATS.join(", ")}`
        );
        return;
      }
      if (files.length) uploadMutation.mutate(files);
      e.target.value = "";
    },
    [uploadMutation]
  );

  const handleSort = useCallback(
    (field: string) => {
      if (sortBy === field) {
        setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
      } else {
        setSortBy(field);
        setSortOrder("asc");
      }
      setPage(1);
    },
    [sortBy]
  );

  const toggleSelect = useCallback((id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (!data) return;
    if (selected.size === data.items.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(data.items.map((a: AudioFile) => a.id)));
    }
  }, [data, selected.size]);

  const handleSearch = useCallback(() => {
    setSearch(searchInput);
    setPage(1);
  }, [searchInput]);

  const sortIndicator = (field: string) => {
    if (sortBy !== field) return "";
    return sortOrder === "asc" ? " ↑" : " ↓";
  };

  const items = data?.items ?? [];
  const totalPages = data?.total_pages ?? 1;

  return (
    <>
      <div className="page-header">
        <h2>音频文件</h2>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={SUPPORTED_FORMATS.join(",")}
            style={{ display: "none" }}
            onChange={handleUpload}
            aria-label="上传音频文件"
          />
          <button
            className="btn-primary"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
          >
            {uploadMutation.isPending ? "上传中..." : "上传文件"}
          </button>
          {selected.size > 0 && (
            <button
              className="btn-danger"
              onClick={() => setShowDeleteConfirm(true)}
            >
              删除选中 ({selected.size})
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="toolbar">
        <input
          type="text"
          placeholder="搜索文件名或转录文本..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          style={{ width: 280 }}
          aria-label="搜索"
        />
        <button onClick={handleSearch}>搜索</button>
      </div>

      {isLoading ? (
        <div className="empty-state">加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          {search ? "未找到匹配结果" : "暂无音频文件"}
        </div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th style={{ width: 40 }}>
                  <input
                    type="checkbox"
                    checked={selected.size === items.length && items.length > 0}
                    onChange={toggleAll}
                    aria-label="全选"
                  />
                </th>
                <th onClick={() => handleSort("filename")}>
                  文件名{sortIndicator("filename")}
                </th>
                <th onClick={() => handleSort("duration")}>
                  时长{sortIndicator("duration")}
                </th>
                <th>格式</th>
                <th>大小</th>
                <th onClick={() => handleSort("upload_time")}>
                  上传时间{sortIndicator("upload_time")}
                </th>
                <th onClick={() => handleSort("transcription_status")}>
                  状态{sortIndicator("transcription_status")}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((audio: AudioFile) => (
                <tr key={audio.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(audio.id)}
                      onChange={() => toggleSelect(audio.id)}
                      aria-label={`选择 ${audio.filename}`}
                    />
                  </td>
                  <td>
                    <Link to={`/audio/${audio.id}`}>{audio.filename}</Link>
                  </td>
                  <td>{formatDuration(audio.duration)}</td>
                  <td>{audio.format}</td>
                  <td>{formatSize(audio.file_size)}</td>
                  <td>{new Date(audio.upload_time).toLocaleString()}</td>
                  <td>
                    <span
                      className={`status-badge status-${audio.transcription_status}`}
                    >
                      {audio.transcription_status}
                    </span>
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
            <button
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}

      {showDeleteConfirm && (
        <div
          className="confirm-dialog-overlay"
          onClick={() => setShowDeleteConfirm(false)}
          role="dialog"
          aria-modal="true"
          aria-label="确认删除"
        >
          <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>确认删除</h3>
            <p>确定要删除选中的 {selected.size} 个文件吗？此操作不可撤销。</p>
            <div className="confirm-dialog-actions">
              <button onClick={() => setShowDeleteConfirm(false)}>取消</button>
              <button
                className="btn-danger"
                onClick={() => deleteMutation.mutate(Array.from(selected))}
                disabled={deleteMutation.isPending}
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
