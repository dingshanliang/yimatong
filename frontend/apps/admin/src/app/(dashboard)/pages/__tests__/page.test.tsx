import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import PagesPage from "../page";

const mockPush = vi.fn();
const mockMutate = vi.fn();
const mockConfirm = vi.fn();
const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: mockMessage,
        modal: { confirm: mockConfirm },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    defaults: { baseURL: "http://localhost:8000/api/v1" },
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: vi.fn(() => ({
    items: [
      {
        id: "tpl-1",
        name: "五常稻花香扫码信任页",
        template_type: "traceability",
        status: "active",
        product_id: null,
        product_name: null,
        display_status: "has_unpublished_draft",
        published_version: { id: "ver-pub", version: 1, status: "published", config_json: { modules: [] } },
        draft_version: { id: "ver-draft", version: 2, status: "draft", config_json: { modules: [] } },
        updated_at: "2026-05-31T10:00:00Z",
      },
    ],
    total: 1,
    page: 1,
    pageSize: 20,
    loading: false,
    filters: {},
    setPage: vi.fn(),
    setFilter: vi.fn(),
    resetFilters: vi.fn(),
    mutate: mockMutate,
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  })),
}));

describe("PagesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/products") {
        return Promise.resolve({ data: { items: [{ id: "p1", name: "五常稻花香大米 5kg" }] } });
      }
      if (url === "/page-templates/industry-templates") {
        return Promise.resolve({
          data: [
            {
              name: "食品溯源页",
              template_type: "traceability",
              description: "食品行业标准模板",
            },
          ],
        });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders the scan page workbench fields and actions", async () => {
    render(<PagesPage />);

    expect(screen.getByText("页面管理")).toBeInTheDocument();
    expect(screen.getByText("管理消费者扫码后看到的 H5 页面")).toBeInTheDocument();
    expect(screen.getByText("页面名称")).toBeInTheDocument();
    expect(screen.getByText("关联产品")).toBeInTheDocument();
    expect(screen.getByText("五常稻花香扫码信任页")).toBeInTheDocument();
    expect(screen.getByText("未关联产品")).toBeInTheDocument();
    expect(screen.getByText("有未发布草稿")).toBeInTheDocument();
    expect(screen.getByText("已发布 v1")).toBeInTheDocument();
    expect(screen.getByText("草稿 v2")).toBeInTheDocument();
    expect(screen.getByText("编辑草稿")).toBeInTheDocument();
    expect(screen.getByText("预览线上页").closest("a")).toHaveAttribute(
      "href",
      "http://localhost:8000/api/v1/page-templates/tpl-1/preview",
    );
  });

  it("opens create flow with blank and industry template starting points", async () => {
    render(<PagesPage />);
    fireEvent.click(screen.getByText("新建页面"));

    await waitFor(() => {
      expect(screen.getByText("从空白页开始")).toBeInTheDocument();
      expect(screen.getByText("从行业模板开始")).toBeInTheDocument();
      expect(mockGet).toHaveBeenCalledWith("/page-templates/industry-templates");
    });
  });

  it("creates a blank page and enters the editor", async () => {
    mockPost
      .mockResolvedValueOnce({ data: { id: "tpl-new" } })
      .mockResolvedValueOnce({ data: { id: "ver-new" } });

    render(<PagesPage />);
    fireEvent.click(screen.getByText("新建页面"));
    fireEvent.change(screen.getByPlaceholderText("例如 五常稻花香扫码信任页"), {
      target: { value: "新扫码页" },
    });
    fireEvent.click(screen.getByText("创建并编辑草稿"));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/page-templates", {
        name: "新扫码页",
        template_type: "traceability",
        product_id: null,
      });
      expect(mockPost).toHaveBeenCalledWith("/page-templates/tpl-new/versions", expect.any(Object));
      expect(mockPush).toHaveBeenCalledWith("/pages/tpl-new/edit");
    });
  });

  it("confirms and publishes the draft from the list", async () => {
    mockPost.mockResolvedValueOnce({ data: {} });
    render(<PagesPage />);

    fireEvent.click(screen.getByText("发布草稿"));

    expect(mockConfirm).toHaveBeenCalledWith(expect.objectContaining({ okText: "发布草稿" }));
    await mockConfirm.mock.calls[0][0].onOk();

    expect(mockPost).toHaveBeenCalledWith("/page-versions/ver-draft/publish");
    expect(mockMutate).toHaveBeenCalled();
  });
});
