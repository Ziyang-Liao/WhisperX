import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listTasks, triggerTranscription } from "../api/transcription";
import type { TranscriptionTask } from "../types";

function formatDurationSec(sec: number | null): string {
  if (sec == null) return "-";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

export default function TranscriptionTasksPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [expandedTask, setExpandedTask] = useState<number | null>(null);
  const [error, setError] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["tasks", page],
    queryFn: () => listTasks({ page, page_size: 20 }),
    refetchInterval: 5000,
  });

  const triggerMutation = useMutation({
    mutationFn: () => triggerTranscription(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      setError("");
    },
    onError: (err: Error) => setError(err.message),
  });

  const items = data?.items ?? [];
  const totalPages = data?.total_pages ?? 1;

  return (
    <>
      <div className="page-header">
        <h2>转录任务</h2>
        <button
          className="btn-primary"
          onClick={() => triggerMutation.mutate()}
          disabled={triggerMutation.isPending}
        >
          {triggerMutation.isPending ? "触发中..." : "手动触发转录"}
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}

      {isLoading ? (
        <div className="empty-state">加载中...</div>
      ) : items.length === 0 ? (
        <div className="empty-state">暂无转录任务</div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>触发方式</th>
                <th>状态</th>
                <th>进度</th>
                <th>成功</th>
                <th>失败</th>
                <th>耗时</th>
                <th>开始时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((task: TranscriptionTask) => {
                const progress =
                  task.total_files > 0
                    ? (task.processed_files / task.total_files) * 100
                    : 0;
                return (
                  <tr
                    key={task.id}
                    onClick={() =>
                      setExpandedTask(
                        expandedTask === task.id ? null : task.id
                      )
                    }
                    style={{ cursor: "pointer" }}
                  >
                    <td>{task.id}</td>
                    <td>
                      {task.trigger_type === "scheduled" ? "定时" : "手动"}
                    </td>
                    <td>
                      <span
                        className={`status-badge status-${task.status}`}
                      >
                        {task.status}
                      </span>
                    </td>
                    <td>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "0.5rem",
                        }}
                      >
                        <div className="task-progress">
                          <div
                            className="task-progress-bar"
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                        <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                          {task.processed_files}/{task.total_files}
                        </span>
                      </div>
                    </td>
                    <td style={{ color: "#065f46" }}>{task.success_count}</td>
                    <td style={{ color: task.failure_count > 0 ? "#991b1b" : undefined }}>
                      {task.failure_count}
                    </td>
                    <td>{formatDurationSec(task.duration_seconds)}</td>
                    <td>{new Date(task.started_at).toLocaleString()}</td>
                  </tr>
                );
              })}
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
    </>
  );
}
