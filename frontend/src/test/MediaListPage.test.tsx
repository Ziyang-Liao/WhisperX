import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import MediaListPage from "../pages/MediaListPage";
import * as api from "../api/media";

vi.mock("../api/media");

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MediaListPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const mediaItem = {
  id: 1,
  filename: "clip.mp4",
  media_type: "video",
  file_size: 1024 * 1024,
  duration: 12,
  format: "mp4",
  source_language: "en",
  transcription_status: "completed",
  error_message: null,
  upload_time: "2026-06-22T10:00:00",
  subtitle_tracks: [{ id: 1, language: "en", is_source: true, status: "completed", error_message: null }],
};

beforeEach(() => {
  vi.mocked(api.listMedia).mockResolvedValue({
    items: [mediaItem], total: 1, page: 1, page_size: 20, total_pages: 1,
  });
  vi.mocked(api.deleteMedia).mockResolvedValue(undefined);
});

describe("MediaListPage", () => {
  it("lists media", async () => {
    renderPage();
    expect(await screen.findByText("clip.mp4")).toBeInTheDocument();
  });

  it("delete requires confirmation — does not delete until confirmed", async () => {
    renderPage();
    await screen.findByText("clip.mp4");

    // Clicking delete opens a confirm dialog, does NOT delete yet.
    fireEvent.click(screen.getByTestId("del-1"));
    expect(screen.getByRole("dialog", { name: "确认删除" })).toBeInTheDocument();
    expect(api.deleteMedia).not.toHaveBeenCalled();

    // Confirming triggers the actual delete (React Query also passes a context
    // arg, so assert on the first arg rather than exact arg list).
    fireEvent.click(screen.getByTestId("confirm-delete"));
    await waitFor(() => expect(api.deleteMedia).toHaveBeenCalled());
    expect(vi.mocked(api.deleteMedia).mock.calls[0][0]).toBe(1);
  });
});
