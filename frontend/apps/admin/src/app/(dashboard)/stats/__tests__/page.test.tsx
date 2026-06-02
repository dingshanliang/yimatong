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

  it("renders stats page title and date picker", async () => {
    render(<StatsPage />);
    await waitFor(() => expect(screen.getByText("扫码统计")).toBeInTheDocument());

    // DatePicker (.ant-picker) should be present
    const picker = document.querySelector(".ant-picker");
    expect(picker).toBeTruthy();
  });

  it("renders stat card values after data loads", async () => {
    render(<StatsPage />);
    // The totals should be 10+15=25 total_scans after data loads
    await waitFor(() => {
      // Look for the aggregated total in the rendered output
      const allText = document.body.textContent ?? "";
      expect(allText).toContain("25");
    }, { timeout: 3000 });
  });

  it("shows export button with loading state", async () => {
    let resolveExport: (value: unknown) => void;
    mockPost.mockImplementation(
      () => new Promise((resolve) => { resolveExport = resolve; })
    );

    render(<StatsPage />);
    await waitFor(() => expect(screen.getByText("扫码统计")).toBeInTheDocument());

    const exportButton = screen.getByRole("button", { name: /导出 Excel/ });
    expect(exportButton).toBeInTheDocument();

    fireEvent.click(exportButton);

    // Button should show loading state after click
    await waitFor(() => expect(exportButton).toHaveClass("ant-btn-loading"));

    // Resolve the export to clean up
    resolveExport!({ data: new ArrayBuffer(8) });
  });
});
