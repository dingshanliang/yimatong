import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import StatsPage from "../../stats/page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();
const mockMessageWarning = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError, warning: mockMessageWarning },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@ant-design/charts", () => ({
  Line: () => null,
}));

describe("StatsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({
          data: [
            { date: "2026-05-26", total_scans: 10, uv: 8, first_scans: 6, rescans: 4 },
            { date: "2026-05-27", total_scans: 15, uv: 12, first_scans: 9, rescans: 6 },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("renders stats page with date filter above stat cards", async () => {
    render(<StatsPage />);
    await waitFor(() => expect(screen.getByText("扫码统计")).toBeInTheDocument());

    // DatePicker (.ant-picker) and stat cards should both be present
    const picker = document.querySelector(".ant-picker");
    expect(picker).toBeTruthy();
    const firstStat = await screen.findByText("总扫码");
    expect(firstStat).toBeInTheDocument();

    // DatePicker should come before stat cards in DOM order
    if (picker && firstStat) {
      const pickerPos = picker.compareDocumentPosition(firstStat);
      // Node.DOCUMENT_POSITION_FOLLOWING = 4, meaning firstStat follows picker
      expect(pickerPos & 4).toBeTruthy();
    }
  });

  it("shows export button with loading state", async () => {
    let resolveExport: (value: unknown) => void;
    mockPost.mockImplementation(
      () => new Promise((resolve) => { resolveExport = resolve; })
    );

    render(<StatsPage />);
    await waitFor(() => expect(screen.getByText("总扫码")).toBeInTheDocument());

    const exportButton = screen.getByRole("button", { name: /导出 Excel/ });
    fireEvent.click(exportButton);

    await waitFor(() => expect(exportButton).toHaveClass("ant-btn-loading"));
    resolveExport!({ data: new ArrayBuffer(8) });
  });
});
