import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AudioListPage from "../pages/AudioListPage";
import type { AudioFile, PaginatedResponse } from "../types";

vi.mock("../api/audio", () => ({
  listAudio: vi.fn(),
  uploadAudio: vi.fn(),
  deleteAudio: vi.fn(),
}));

import { listAudio, uploadAudio, deleteAudio } from "../api/audio";

const mockListAudio = vi.mocked(listAudio);
const mockUploadAudio = vi.mocked(uploadAudio);
const mockDeleteAudio = vi.mocked(deleteAudio);

function makeAudio(overrides: Partial<AudioFile> = {}): AudioFile {
  return {
    id: 1,
    filename: "test.wav",
    file_size: 1024,
    duration: 60,
    format: "wav",
    upload_time: "2025-01-01T00:00:00Z",
    transcription_status: "pending",
    ...overrides,
  };
}

function makePage(
  items: AudioFile[],
  total?: number
): PaginatedResponse<AudioFile> {
  return {
    items,
    total: total ?? items.length,
    page: 1,
    page_size: 20,
    total_pages: Math.ceil((total ?? items.length) / 20) || 1,
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AudioListPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AudioListPage", () => {
  it("renders audio list with data", async () => {
    mockListAudio.mockResolvedValue(
      makePage([
        makeAudio({ id: 1, filename: "song.mp3", format: "mp3" }),
        makeAudio({ id: 2, filename: "voice.wav", format: "wav" }),
      ])
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("song.mp3")).toBeInTheDocument();
      expect(screen.getByText("voice.wav")).toBeInTheDocument();
    });
  });

  it("shows empty state when no files", async () => {
    mockListAudio.mockResolvedValue(makePage([]));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("暂无音频文件")).toBeInTheDocument();
    });
  });

  it("sends search param when searching", async () => {
    mockListAudio.mockResolvedValue(makePage([]));
    renderPage();
    await waitFor(() => screen.getByText("暂无音频文件"));

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("搜索文件名或转录文本..."), "nonexistent");
    await user.click(screen.getByText("搜索"));

    await waitFor(() => {
      expect(mockListAudio).toHaveBeenCalledWith(
        expect.objectContaining({ search: "nonexistent" })
      );
    });
  });

  it("calls listAudio with sort params when clicking column headers", async () => {
    mockListAudio.mockResolvedValue(makePage([makeAudio()]));
    renderPage();
    await waitFor(() => screen.getByText("test.wav"));

    const user = userEvent.setup();
    await user.click(screen.getByText(/文件名/));

    await waitFor(() => {
      expect(mockListAudio).toHaveBeenCalledWith(
        expect.objectContaining({ sort_by: "filename", sort_order: "asc" })
      );
    });
  });

  it("handles pagination", async () => {
    mockListAudio.mockResolvedValue({
      items: [makeAudio()],
      total: 40,
      page: 1,
      page_size: 20,
      total_pages: 2,
    });
    renderPage();
    await waitFor(() => screen.getByText("1 / 2"));

    const user = userEvent.setup();
    await user.click(screen.getByText("下一页"));

    await waitFor(() => {
      expect(mockListAudio).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 })
      );
    });
  });

  it("has accept attribute restricting to supported formats", () => {
    mockListAudio.mockResolvedValue(makePage([]));
    renderPage();

    const input = screen.getByLabelText("上传音频文件") as HTMLInputElement;
    expect(input.accept).toBe(".wav,.mp3,.flac,.m4a,.ogg");
  });

  it("uploads valid files successfully", async () => {
    mockListAudio.mockResolvedValue(makePage([]));
    mockUploadAudio.mockResolvedValue([makeAudio({ filename: "new.wav" })]);
    renderPage();
    await waitFor(() => screen.getByText("暂无音频文件"));

    const user = userEvent.setup();
    const input = screen.getByLabelText("上传音频文件");
    const goodFile = new File(["data"], "new.wav", { type: "audio/wav" });
    await user.upload(input, goodFile);

    await waitFor(() => {
      expect(mockUploadAudio).toHaveBeenCalled();
    });
  });

  it("shows delete confirmation dialog with correct content", async () => {
    mockListAudio.mockResolvedValue(
      makePage([makeAudio({ id: 1, filename: "to-delete.wav" })])
    );
    renderPage();
    await waitFor(() => screen.getByText("to-delete.wav"));

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("选择 to-delete.wav"));
    await user.click(screen.getByText(/删除选中/));

    // Use the dialog role to scope queries
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/确定要删除选中的 1 个文件吗/)).toBeInTheDocument();
  });

  it("executes delete after confirmation", async () => {
    mockListAudio.mockResolvedValue(
      makePage([makeAudio({ id: 5, filename: "bye.wav" })])
    );
    mockDeleteAudio.mockResolvedValue(undefined);
    renderPage();
    await waitFor(() => screen.getByText("bye.wav"));

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("选择 bye.wav"));
    await user.click(screen.getByText(/删除选中/));

    // Click the confirm button inside the dialog
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(mockDeleteAudio).toHaveBeenCalledWith(5);
    });
  });
});
