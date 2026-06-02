import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import DashboardHome from "../../_components/DashboardHome";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
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
  Pie: () => null,
}));

describe("DashboardHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.resolve({
          data: {
            today_scans: 10,
            cumulative_scans: 100,
            cumulative_first_scans: 80,
            trend: [],
            environment_breakdown: {},
          },
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders dashboard statistics after loading", async () => {
    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("今日扫码")).toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("exports xlsx with correct MIME type and file extension", async () => {
    const mockCreateObjectURL = vi.fn(() => "blob:mock-url");
    const mockRevokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: mockCreateObjectURL,
      revokeObjectURL: mockRevokeObjectURL,
    });

    // Spy on appendChild before clicking to capture the <a> element before it's removed
    const appendSpy = vi.spyOn(document.body, "appendChild");

    const xlsxBuffer = new ArrayBuffer(8);
    mockPost.mockResolvedValueOnce({
      data: xlsxBuffer,
    });

    render(<DashboardHome />);

    const exportButton = await screen.findByRole("button", { name: /导出扫码数据/ });
    fireEvent.click(exportButton);

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      "/analytics/exports",
      null,
      expect.objectContaining({
        params: expect.objectContaining({ export_type: "scan_events" }),
        responseType: "blob",
      }),
    );

    await waitFor(() => expect(mockCreateObjectURL).toHaveBeenCalledTimes(1));
    const blobArg = mockCreateObjectURL.mock.calls[0][0];
    expect(blobArg).toBeInstanceOf(Blob);
    expect(blobArg.type).toBe(
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );

    // The <a> element is appended then immediately removed, so we use the spy
    await waitFor(() => {
      const linkCalls = appendSpy.mock.calls.filter(
        (call) => (call[0] as HTMLElement).tagName === "A",
      );
      expect(linkCalls.length).toBe(1);
    });
    const linkCall = appendSpy.mock.calls.find(
      (call) => (call[0] as HTMLElement).tagName === "A",
    );
    const linkEl = linkCall![0] as HTMLAnchorElement;
    expect(linkEl.download).toMatch(/\.xlsx$/);

    appendSpy.mockRestore();
    vi.unstubAllGlobals();
  });
});
