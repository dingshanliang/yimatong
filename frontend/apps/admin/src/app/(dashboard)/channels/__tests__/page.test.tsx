import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import ChannelsPage, { PROVINCE_CITY_OPTIONS } from "../page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();
const mockModalConfirm = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
        modal: { confirm: mockModalConfirm },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();
let mockUser: {
  tenant_type: string;
  role: string;
  acting_tenant_id: string | null;
} | null = { tenant_type: "brand", role: "admin", acting_tenant_id: null };

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mockUser }) => unknown) =>
    selector({ user: mockUser }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

function paginated(items: unknown[]) {
  return { data: { items, total: items.length, page: 1, page_size: 20 } };
}

function clickSelectOption(text: string) {
  const options = Array.from(
    document.querySelectorAll(".ant-select-item-option-content")
  ).filter((item) => item.textContent === text);
  fireEvent.click(options.at(-1) as HTMLElement);
}

describe("ChannelsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { tenant_type: "brand", role: "admin", acting_tenant_id: null };
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants/me") {
        return Promise.resolve({
          data: { enabled_features: { channel_portal: true } },
        });
      }
      if (url === "/channels/overview") {
        return Promise.resolve({
          data: {
            distributor_count: 2,
            region_count: 3,
            store_count: 5,
            allocated_quantity: 1200,
            pending_diversion_count: 4,
          },
        });
      }
      if (url === "/channels/distributors") {
        return Promise.resolve(
          paginated([
            {
              id: "d1",
              name: "华东经销商",
              code: "DIST-EAST",
              status: "active",
              region_count: 2,
              store_count: 4,
              allocated_quantity: 800,
            },
          ])
        );
      }
      if (url === "/channels/regions") {
        return Promise.resolve(
          paginated([
            {
              id: "r1",
              name: "上海区域",
              code: "REG-SH",
              province: "上海",
              city: "上海",
              coverage_type: "city",
              coverage_areas: [{ province: "上海", city: "上海" }],
              coverage_label: "上海",
              status: "active",
              distributor_id: "d1",
              distributor_name: "华东经销商",
              store_count: 2,
              allocated_quantity: 300,
            },
          ])
        );
      }
      if (url === "/channels/stores") {
        return Promise.resolve(
          paginated([
            {
              id: "s1",
              name: "南京东路店",
              code: "STORE-NJDL",
              region_id: "r1",
              region_name: "上海区域",
              distributor_id: "d1",
              distributor_name: "华东经销商",
              allocated_quantity: 300,
              status: "active",
            },
          ])
        );
      }
      if (url === "/code-batches") {
        return Promise.resolve(
          paginated([
            {
              id: "b1",
              batch_code: "CB-001",
              quantity: 1000,
              product_name: "有机大米",
              sku_name: "5kg",
            },
          ])
        );
      }
      if (url === "/channels/code-allocations") {
        return Promise.resolve(
          paginated([
            {
              id: "a1",
              allocation_root_id: "a1",
              version: 1,
              action: "allocate",
              status: "active",
              target_type: "store",
              store_id: "s1",
              effective_to: null,
              batch_id: "b1",
              batch_code: "CB-001",
              product_name: "有机大米",
              sku_name: "5kg",
              store_name: "南京东路店",
              distributor_name: "华东经销商",
              region_name: "上海区域",
              quantity: 300,
              batch_quantity: 1000,
              remaining_quantity: 700,
              allocated_at: "2026-06-01T10:00:00Z",
            },
          ])
        );
      }
      if (url.startsWith("/channels/diversion-clues")) {
        return Promise.resolve(
          paginated([
            {
              id: "c1",
              public_id: "QR001",
              expected_region: "上海",
              detected_city: "北京",
              severity: "high",
              product_name: "有机大米",
              sku_name: "5kg",
              batch_code: "CB-001",
              distributor_id: "d1",
              distributor_name: "华东经销商",
              region_id: "r1",
              region_name: "上海区域",
              store_name: "南京东路店",
              resolved: false,
              version: 1,
              investigation_status: "open",
              observation_count: 1,
              resolution_action: null,
              resolution_note: null,
            },
          ])
        );
      }
      if (url === "/risk-dashboard/diversion-clues/c1/investigation") {
        return Promise.resolve({
          data: {
            clue_id: "c1",
            version: 1,
            investigation_status: "open",
            resolved: false,
            evidence: [],
            history: [],
          },
        });
      }
      if (url === "/accounts") {
        return Promise.resolve({
          data: [{ id: "acc1", name: "经销商账号", email: "dist@test.com" }],
        });
      }
      if (url === "/channels/account-scopes") {
        return Promise.resolve({ data: [] });
      }
      return Promise.resolve(paginated([]));
    });
    mockPatch.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
    mockPut.mockResolvedValue({ data: {} });
  });

  it.each([
    { tenant_type: "brand", role: "viewer", acting_tenant_id: null },
    { tenant_type: "agency", role: "admin", acting_tenant_id: "brand-1" },
  ])("denies unsupported principals before any request", async (principal) => {
    mockUser = principal;
    render(<ChannelsPage />);
    expect(screen.getByText("当前账号无渠道管理权限")).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("keeps operator management and allocation but makes zero scope requests", async () => {
    mockUser = {
      tenant_type: "brand",
      role: "operator",
      acting_tenant_id: null,
    };
    render(<ChannelsPage />);
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("/channels/overview")
    );
    expect(mockGet).not.toHaveBeenCalledWith("/accounts");
    expect(mockGet).not.toHaveBeenCalledWith("/channels/account-scopes");
    expect(
      screen.queryByRole("tab", { name: "账号授权" })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /新建经销商/ })
    ).toBeInTheDocument();
  });

  it("renders overview metrics and business-readable master data", async () => {
    render(<ChannelsPage />);

    expect(
      await screen.findByRole("tab", { name: "门店" })
    ).toBeInTheDocument();

    expect(screen.getByText("渠道管理")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByText("已登记码量").length).toBeGreaterThan(0)
    );
    expect(screen.getAllByText("华东经销商").length).toBeGreaterThan(0);
    expect(screen.getByText("2 个区域 / 4 个门店")).toBeInTheDocument();
    expect(screen.getByText("800 个码")).toBeInTheDocument();
  });

  it("creates distributor without manual code and shows next-step actions", async () => {
    mockPost.mockImplementation(
      (url: string, body: Record<string, unknown>) => {
        if (url === "/channels/distributors") {
          return Promise.resolve({
            data: {
              id: "d-new",
              name: String(body.name),
              code: "DIST-20260601-001",
              contact_name: body.contact_name,
              contact_phone_masked: "138****8000",
              status: body.status || "active",
              region_count: 0,
              store_count: 0,
              allocated_quantity: 0,
            },
          });
        }
        return Promise.resolve({ data: {} });
      }
    );

    render(<ChannelsPage />);

    fireEvent.click(await screen.findByRole("button", { name: /新建经销商/ }));
    const dialog = await screen.findByRole("dialog", { name: "新建经销商" });
    expect(
      within(dialog).getByText("编码将在创建后自动生成")
    ).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("编码")).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("名称"), {
      target: { value: "华南经销商" },
    });
    fireEvent.change(within(dialog).getByLabelText("联系人"), {
      target: { value: "张三" },
    });
    fireEvent.change(within(dialog).getByLabelText("联系电话"), {
      target: { value: "13800138000" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建经销商" }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/channels/distributors",
        expect.not.objectContaining({ code: expect.anything() }),
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
    expect(await screen.findByText("DIST-20260601-001")).toBeInTheDocument();
    const successDialog = screen.getByRole("dialog", { name: "新建经销商" });
    expect(
      within(successDialog).getByRole("button", { name: "创建区域" })
    ).toBeInTheDocument();
    expect(
      within(successDialog).getByRole("button", { name: "绑定入口账号" })
    ).toBeInTheDocument();
    expect(
      within(successDialog).getByRole("button", { name: "登记流向" })
    ).toBeInTheDocument();
  }, 10000);

  it("prefills distributor context and suggests region name from structured city", async () => {
    render(<ChannelsPage />);

    await screen.findByText("华东经销商");
    fireEvent.click(screen.getAllByRole("button", { name: "创建区域" })[0]);
    const regionDialog = await screen.findByRole("dialog", {
      name: "新建区域",
    });
    expect(
      within(regionDialog).getByText("编码将在创建后自动生成")
    ).toBeInTheDocument();

    fireEvent.mouseDown(within(regionDialog).getByLabelText("省份"));
    await screen.findByTitle("上海");
    clickSelectOption("上海");
    fireEvent.mouseDown(within(regionDialog).getByLabelText("城市"));
    await waitFor(() =>
      expect(
        Array.from(
          document.querySelectorAll(".ant-select-item-option-content")
        ).some((item) => item.textContent === "上海")
      ).toBe(true)
    );
    clickSelectOption("上海");

    await waitFor(() =>
      expect(within(regionDialog).getByLabelText("名称")).toHaveValue(
        "上海区域"
      )
    );
    fireEvent.change(within(regionDialog).getByLabelText("名称"), {
      target: { value: "上海核心区域" },
    });
    expect(
      within(regionDialog).getByLabelText("所属经销商")
    ).toBeInTheDocument();
  }, 10000);

  it("creates province-level regions without forcing a city", async () => {
    render(<ChannelsPage />);

    await screen.findByText("华东经销商");
    fireEvent.click(screen.getAllByRole("button", { name: "创建区域" })[0]);
    const regionDialog = await screen.findByRole("dialog", {
      name: "新建区域",
    });

    fireEvent.mouseDown(within(regionDialog).getByLabelText("覆盖类型"));
    await screen.findByTitle("省级片区");
    clickSelectOption("省级片区");
    await waitFor(() =>
      expect(
        within(regionDialog).queryByLabelText("城市")
      ).not.toBeInTheDocument()
    );

    fireEvent.mouseDown(within(regionDialog).getByLabelText("省份"));
    await screen.findByTitle("北京");
    clickSelectOption("北京");

    await waitFor(() =>
      expect(within(regionDialog).getByLabelText("名称")).toHaveValue(
        "北京省区"
      )
    );
    expect(
      within(regionDialog).queryByLabelText("城市")
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(regionDialog).getByRole("button", { name: "创建区域" })
    );

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/channels/regions",
        expect.objectContaining({
          coverage_type: "province",
          province: "北京",
          city: null,
          coverage_areas: [{ province: "北京", city: null }],
        }),
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  }, 10000);

  it("supports multi-province regions for large-area coverage", async () => {
    render(<ChannelsPage />);

    await screen.findByText("华东经销商");
    fireEvent.click(screen.getAllByRole("button", { name: "创建区域" })[0]);
    const regionDialog = await screen.findByRole("dialog", {
      name: "新建区域",
    });

    fireEvent.mouseDown(within(regionDialog).getByLabelText("覆盖类型"));
    await screen.findByTitle("大区片区");
    clickSelectOption("大区片区");
    await waitFor(() =>
      expect(
        within(regionDialog).getByLabelText("覆盖省份")
      ).toBeInTheDocument()
    );

    expect(
      within(regionDialog).queryByLabelText("省份")
    ).not.toBeInTheDocument();
    expect(
      within(regionDialog).queryByLabelText("城市")
    ).not.toBeInTheDocument();
    fireEvent.mouseDown(within(regionDialog).getByLabelText("覆盖省份"));
    await screen.findByTitle("北京");
    clickSelectOption("北京");
    fireEvent.mouseDown(within(regionDialog).getByLabelText("覆盖省份"));
    await screen.findByTitle("天津");
    clickSelectOption("天津");
    fireEvent.mouseDown(within(regionDialog).getByLabelText("覆盖省份"));
    await screen.findByTitle("河北");
    clickSelectOption("河北");

    await waitFor(() =>
      expect(within(regionDialog).getByLabelText("名称")).toHaveValue(
        "北京、天津等大区"
      )
    );
    fireEvent.click(
      within(regionDialog).getByRole("button", { name: "创建区域" })
    );

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/channels/regions",
        expect.objectContaining({
          coverage_type: "multi_province",
          province: "北京",
          city: null,
          coverage_areas: [
            { province: "北京", city: null },
            { province: "天津", city: null },
            { province: "河北", city: null },
          ],
        }),
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  }, 10000);

  it("contains complete province city options for region creation", () => {
    const byProvince = new Map(
      PROVINCE_CITY_OPTIONS.map((item) => [item.province, item.cities])
    );

    expect(PROVINCE_CITY_OPTIONS.length).toBeGreaterThanOrEqual(31);
    expect(byProvince.get("四川")).toContain("成都");
    expect(byProvince.get("湖北")).toContain("武汉");
    expect(byProvince.get("新疆")).toContain("乌鲁木齐");
    expect(byProvince.get("海南")).toContain("三亚");
  });

  it("shows allocation form with readable batch capacity and submission summary", async () => {
    render(<ChannelsPage />);

    fireEvent.click(screen.getByRole("tab", { name: "流向登记" }));
    await waitFor(() => expect(screen.getByText("CB-001")).toBeInTheDocument());
    const allocationButton = await screen.findByText("新建流向");
    fireEvent.click(allocationButton.closest("button") as HTMLButtonElement);
    await waitFor(() =>
      expect(
        screen.getByRole("dialog", { name: "新建流向登记" })
      ).toBeInTheDocument()
    );
    const allocationDialog = screen.getByRole("dialog", {
      name: "新建流向登记",
    });

    expect(
      within(allocationDialog).getByText(
        "记录这批已赋码货品发往哪个渠道，不开放打印或下载码包。"
      )
    ).toBeInTheDocument();
    expect(within(allocationDialog).getByText("批次号")).toBeInTheDocument();
    expect(
      within(allocationDialog).getAllByText("CB-001").length
    ).toBeGreaterThanOrEqual(2);
    expect(within(allocationDialog).getByText("产品/SKU")).toBeInTheDocument();
    expect(
      within(allocationDialog).getByText("有机大米 / 5kg")
    ).toBeInTheDocument();
    expect(within(allocationDialog).getByText("总量 1000")).toBeInTheDocument();
    expect(
      within(allocationDialog).getByText("已登记 300")
    ).toBeInTheDocument();
    expect(within(allocationDialog).getByText("剩余 700")).toBeInTheDocument();
    expect(
      within(allocationDialog).getByLabelText("流向层级")
    ).toBeInTheDocument();
    expect(
      within(allocationDialog).getByLabelText("选择区域")
    ).toBeInTheDocument();
    expect(
      within(allocationDialog).getByText("系统已根据区域带出经销商：华东经销商")
    ).toBeInTheDocument();
    expect(
      within(allocationDialog).getByText("可登记 1-700 个")
    ).toBeInTheDocument();

    fireEvent.click(
      within(allocationDialog).getByRole("button", { name: "全部登记" })
    );
    await waitFor(() =>
      expect(
        within(allocationDialog).getByRole("spinbutton", { name: "登记数量" })
      ).toHaveValue("700")
    );
    expect(
      within(allocationDialog).getByText(
        "将 CB-001 的 700 个已赋码货品登记到 上海区域 / 上海，归属经销商 华东经销商。"
      )
    ).toBeInTheDocument();
  });

  it("uses clear allocation target labels and keeps store target discoverable", async () => {
    render(<ChannelsPage />);

    fireEvent.click(screen.getByRole("tab", { name: "流向登记" }));
    await waitFor(() => expect(screen.getByText("CB-001")).toBeInTheDocument());
    const allocationButton = await screen.findByText("新建流向");
    fireEvent.click(allocationButton.closest("button") as HTMLButtonElement);
    const allocationDialog = await screen.findByRole("dialog", {
      name: "新建流向登记",
    });

    fireEvent.mouseDown(within(allocationDialog).getByLabelText("流向层级"));
    await waitFor(() =>
      expect(screen.getAllByTitle("区域").length).toBeGreaterThan(1)
    );
    expect(await screen.findByTitle("门店")).toBeInTheDocument();
    clickSelectOption("门店");

    expect(
      await within(allocationDialog).findByLabelText("选择门店")
    ).toBeInTheDocument();
    expect(
      within(allocationDialog).getByText(
        "门店入口可查看本次收货批次和扫码趋势，不开放打印或下载码包。"
      )
    ).toBeInTheDocument();
  });

  it("shows allocation records with business names instead of UUID-only values", async () => {
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "流向登记" }));

    await waitFor(() => expect(screen.getByText("CB-001")).toBeInTheDocument());
    expect(screen.getByText("有机大米 / 5kg")).toBeInTheDocument();
    expect(screen.getByText("南京东路店")).toBeInTheDocument();
    expect(screen.getByText("剩余 700")).toBeInTheDocument();
  });

  it("lets operators reassign the current allocation with CAS, reason and idempotency", async () => {
    mockUser = {
      tenant_type: "brand",
      role: "operator",
      acting_tenant_id: null,
    };
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "流向登记" }));

    fireEvent.click(await screen.findByRole("button", { name: "重分配" }));
    const dialog = await screen.findByRole("dialog", { name: "重分配流向" });
    fireEvent.change(within(dialog).getByLabelText("重分配原因"), {
      target: { value: "配送路线更正" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认登记" }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/channels/code-allocations/a1/reassign",
        expect.objectContaining({
          expected_version: 1,
          target_type: "store",
          store_id: "s1",
          quantity: 300,
          reason: "配送路线更正",
        }),
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  });

  it("requires an audited reason when archiving the current allocation", async () => {
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "流向登记" }));

    const row = (await screen.findByText("CB-001")).closest(
      "tr"
    ) as HTMLTableRowElement;
    fireEvent.click(within(row).getByRole("button", { name: "归档" }));
    const archiveTitle = await screen.findByText("归档流向登记");
    const archiveDialog = archiveTitle.closest(
      '[role="dialog"]'
    ) as HTMLElement;
    const reasonInput = within(archiveDialog).getByRole("textbox");
    fireEvent.change(reasonInput, {
      target: { value: "本批次流向登记作废" },
    });
    fireEvent.click(
      within(archiveDialog).getByRole("button", { name: "确认归档" })
    );

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/channels/code-allocations/a1/archive",
        { expected_version: 1, reason: "本批次流向登记作废" },
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  }, 10000);

  it("shows diversion clues with business context and resolves with action", async () => {
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "窜货线索" }));

    expect(await screen.findByText("高风险")).toBeInTheDocument();
    expect(screen.getByText("上海 → 北京")).toBeInTheDocument();
    expect(screen.getByText("有机大米 / 5kg")).toBeInTheDocument();
    expect(screen.getByText("CB-001")).toBeInTheDocument();
    expect(screen.getAllByText("华东经销商").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByRole("button", { name: /查看处理/ }));
    expect(await screen.findByText("窜货线索处理")).toBeInTheDocument();
    expect(screen.getAllByText("异常路径").length).toBeGreaterThan(0);
    expect(screen.getAllByText("关联货品").length).toBeGreaterThan(0);
    expect(screen.getByText("建议动作")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByLabelText("处理结果"));
    await screen.findByTitle("确认窜货");
    clickSelectOption("确认窜货");
    fireEvent.change(screen.getByPlaceholderText("填写处理记录"), {
      target: { value: "已联系经销商核实" },
    });
    fireEvent.click(screen.getByRole("button", { name: /标记为已处理/ }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/risk-dashboard/diversion-clues/c1/transition",
        {
          expected_version: 1,
          to_status: "confirmed_diversion",
          reason: "已联系经销商核实",
          resolution_note: "已联系经销商核实",
        },
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  });
});
