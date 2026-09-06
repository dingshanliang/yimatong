import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CompliancePage from "../page";

const mockMessage = { success: vi.fn(), error: vi.fn() };
const mockGet = vi.fn();
const mockPatch = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mockMessage }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
  registerAuthInterceptorHandlers: vi.fn(),
}));

describe("CompliancePage privacy policy preview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({
      data: {
        compliance_settings: {
          privacy_version: "1.0",
          privacy_content: "已保存的隐私政策",
        },
      },
    });
    mockPatch.mockResolvedValue({ data: {} });
  });

  it("previews unsaved form values with unsafe HTML removed and keeps the draft after closing", async () => {
    render(<CompliancePage />);

    const versionInput = await screen.findByLabelText("版本号");
    const contentInput = screen.getByLabelText("隐私政策内容");

    fireEvent.change(versionInput, { target: { value: "2.1" } });
    fireEvent.change(contentInput, {
      target: {
        value:
          '<p>新的未保存政策</p><img src="x" onerror="window.__unsafe = true"><script>危险脚本</script>',
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /预览消费者效果/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("版本 2.1")).toBeInTheDocument();
    expect(within(dialog).getByText("新的未保存政策")).toBeInTheDocument();
    expect(within(dialog).queryByText("危险脚本")).not.toBeInTheDocument();
    expect(dialog.querySelector('img[src="x"]')).not.toHaveAttribute("onerror");
    expect(mockPatch).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: /关.*闭/ }));

    expect(versionInput).toHaveValue("2.1");
    expect(contentInput).toHaveValue(
      '<p>新的未保存政策</p><img src="x" onerror="window.__unsafe = true"><script>危险脚本</script>'
    );
  });

  it("explains why an empty draft cannot be previewed", async () => {
    render(<CompliancePage />);

    const contentInput = await screen.findByLabelText("隐私政策内容");
    fireEvent.change(contentInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /预览消费者效果/ }));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("暂无可预览内容，请先填写隐私政策。")
    ).toBeInTheDocument();
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("preserves line breaks in plain-text policy drafts", async () => {
    render(<CompliancePage />);

    const contentInput = await screen.findByLabelText("隐私政策内容");
    fireEvent.change(contentInput, { target: { value: "第一条\n第二条" } });
    fireEvent.click(screen.getByRole("button", { name: /预览消费者效果/ }));

    const dialog = await screen.findByRole("dialog");
    const plainTextPreview = dialog.querySelector("p.whitespace-pre-wrap");
    expect(plainTextPreview).toHaveTextContent("第一条 第二条");
    expect(plainTextPreview).toHaveClass("whitespace-pre-wrap");
  });
});
