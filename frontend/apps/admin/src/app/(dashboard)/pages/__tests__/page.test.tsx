import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import PagesPage from "../page";

// Mock Ant Design message
vi.mock("antd", async () => {
  const actual = await vi.importActual("antd");
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
    // Popconfirm in jsdom does not show a popup; mock it to call onConfirm immediately
    Popconfirm: ({ children, onConfirm }: { children: React.ReactNode; onConfirm?: () => void }) => (
      <span onClick={onConfirm}>{children}</span>
    ),
  };
});

// Mock api
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
}));

// Mock hooks
vi.mock("@/lib/hooks", () => ({
  usePaginatedList: vi.fn(() => ({
    items: [
      {
        id: "tpl-1",
        name: "测试模板",
        template_type: "product_info",
        status: "active",
        published_version: null,
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    setPage: vi.fn(),
    refresh: vi.fn(),
  })),
}));

describe("PagesPage DSL Editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders page list with template data", () => {
    render(<PagesPage />);
    expect(screen.getByText("页面管理")).toBeInTheDocument();
    expect(screen.getByText("测试模板")).toBeInTheDocument();
  });

  it("opens version management modal when clicking 版本管理", async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        {
          id: "ver-1",
          version: 1,
          status: "draft",
          config_json: { modules: [] },
          created_at: "2026-05-29T10:00:00Z",
        },
      ],
    });

    render(<PagesPage />);
    const versionBtn = screen.getByText("版本管理");
    fireEvent.click(versionBtn);

    await waitFor(() => {
      expect(screen.getByText("版本管理 — 测试模板")).toBeInTheDocument();
    });
  });

  it("opens DSL editor drawer when clicking 编辑 on a draft version", async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        {
          id: "ver-1",
          version: 1,
          status: "draft",
          config_json: {
            modules: [
              { id: "hero", type: "product_hero", enabled: true, config: {} },
            ],
          },
          created_at: "2026-05-29T10:00:00Z",
        },
      ],
    });

    render(<PagesPage />);
    const versionBtn = screen.getByText("版本管理");
    fireEvent.click(versionBtn);

    await waitFor(() => {
      expect(screen.getByText("版本管理 — 测试模板")).toBeInTheDocument();
    });

    // Find the edit button within the modal
    const modal = screen.getByRole("dialog");
    const editBtn = within(modal).getByText("编辑");
    fireEvent.click(editBtn);

    await waitFor(() => {
      expect(screen.getByText("页面 DSL 编辑")).toBeInTheDocument();
    });
  });

  it("preview button links to correct API URL with full base", () => {
    render(<PagesPage />);
    const previewLink = screen.getByTestId("preview-link");
    const href = previewLink.getAttribute("href");
    expect(href).toMatch(/^http:\/\/localhost:8000\/api\/v1\/page-templates\/tpl-1\/preview$/);
  });

  it("saves DSL draft successfully", async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        {
          id: "ver-1",
          version: 1,
          status: "draft",
          config_json: { modules: [] },
          created_at: "2026-05-29T10:00:00Z",
        },
      ],
    });
    mockPatch.mockResolvedValueOnce({ data: { id: "ver-1" } });

    render(<PagesPage />);
    fireEvent.click(screen.getByText("版本管理"));

    await waitFor(() => {
      expect(screen.getByText("版本管理 — 测试模板")).toBeInTheDocument();
    });

    const modal = screen.getByRole("dialog");
    fireEvent.click(within(modal).getByText("编辑"));

    await waitFor(() => {
      expect(screen.getByText("页面 DSL 编辑")).toBeInTheDocument();
    });

    // Click save
    const saveBtn = screen.getByText("保存");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/page-versions/ver-1", {
        config_json: { modules: [] },
      });
    });
  });

  it("publishes a draft version", async () => {
    mockGet.mockResolvedValueOnce({
      data: [
        {
          id: "ver-1",
          version: 1,
          status: "draft",
          config_json: { modules: [] },
          created_at: "2026-05-29T10:00:00Z",
        },
      ],
    });
    mockPost.mockResolvedValueOnce({ data: {} });

    render(<PagesPage />);
    fireEvent.click(screen.getByText("版本管理"));

    await waitFor(() => {
      expect(screen.getByText("版本管理 — 测试模板")).toBeInTheDocument();
    });

    const modal = screen.getByRole("dialog");
    const publishBtn = within(modal).getByText("发布");
    fireEvent.click(publishBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/page-versions/ver-1/publish");
    });
  });
});
