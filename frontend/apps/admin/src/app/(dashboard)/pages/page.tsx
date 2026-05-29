"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table, Button, Space, Modal, Form, Input, InputNumber, Select, Tag, Typography,
  message, Popconfirm, Drawer, Tabs, Switch, DatePicker, Tooltip,
} from "antd";
import {
  PlusOutlined, EyeOutlined, SendOutlined, RollbackOutlined,
  EditOutlined, StopOutlined, CodeOutlined, CheckCircleOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import {
  validateDSL, createEmptyDSL, createDefaultModules,
  MODULE_TYPES, MODULE_TYPE_LABELS,
  type PageDSL, type ModuleConfig, type ModuleType,
} from "@/lib/page-dsl";
import dayjs from "dayjs";

const { Title, Text } = Typography;
const { TextArea } = Input;

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
  description?: string;
  published_version?: PageVersion | null;
}

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

const TYPE_LABELS: Record<string, string> = {
  product_info: "产品信息",
  traceability: "溯源页",
  brand_story: "品牌故事",
  campaign: "活动页",
};

const VERSION_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  published: { label: "已发布", color: "blue" },
  archived: { label: "已归档", color: "gray" },
  offline: { label: "已下线", color: "orange" },
};

export default function PagesPage() {
  const [templates, setTemplates] = useState<PageTemplate[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [industryOpen, setIndustryOpen] = useState(false);
  const [industryTemplates, setIndustryTemplates] = useState<Record<string, unknown>[]>([]);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [currentTemplate, setCurrentTemplate] = useState<PageTemplate | null>(null);
  const [versions, setVersions] = useState<PageVersion[]>([]);
  const [dslDrawerOpen, setDslDrawerOpen] = useState(false);
  const [editingVersion, setEditingVersion] = useState<PageVersion | null>(null);
  const [dslText, setDslText] = useState("");
  const [dslErrors, setDslErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // ─── 模板列表 ───────────────────────────────────

  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/page-templates", {
        params: { page, page_size: 20 },
      });
      setTemplates(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载页面列表失败");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

  // ─── 创建模板 ───────────────────────────────────

  const handleCreate = async (values: Record<string, string>) => {
    try {
      const { data: tpl } = await api.post("/page-templates", values);
      // 自动创建第一个草稿版本
      const initialDSL = createEmptyDSL();
      initialDSL.modules = createDefaultModules();
      await api.post(`/page-templates/${tpl.id}/versions`, {
        config_json: initialDSL,
      });
      message.success("页面模板创建成功，已生成初始草稿");
      setCreateOpen(false);
      form.resetFields();
      fetchTemplates();
    } catch {
      message.error("创建失败");
    }
  };

  // ─── 行业模板 ───────────────────────────────────

  const fetchIndustryTemplates = async () => {
    try {
      const { data } = await api.get("/page-templates/industry-templates");
      setIndustryTemplates(data);
    } catch { /* ignore */ }
  };

  const cloneTemplate = async (index: number) => {
    try {
      await api.post(`/page-templates/industry-templates/${index}/clone`);
      message.success("模板复制成功");
      setIndustryOpen(false);
      fetchTemplates();
    } catch {
      message.error("复制失败");
    }
  };

  // ─── 版本管理 ───────────────────────────────────

  const showVersions = async (tmpl: PageTemplate) => {
    setCurrentTemplate(tmpl);
    try {
      const { data } = await api.get(`/page-templates/${tmpl.id}/versions`);
      setVersions(data);
      setVersionsOpen(true);
    } catch {
      message.error("加载版本失败");
    }
  };

  const refreshVersions = async () => {
    if (!currentTemplate) return;
    try {
      const { data } = await api.get(`/page-templates/${currentTemplate.id}/versions`);
      setVersions(data);
    } catch { /* ignore */ }
  };

  // ─── 状态机操作 ─────────────────────────────────

  const publishVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/publish`);
      message.success("发布成功");
      refreshVersions();
      fetchTemplates();
    } catch {
      message.error("发布失败");
    }
  };

  const archiveVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/archive`);
      message.success("已下线");
      refreshVersions();
      fetchTemplates();
    } catch {
      message.error("下线失败");
    }
  };

  const rollbackVersion = async (templateId: string, versionId: string) => {
    try {
      await api.post(`/page-templates/${templateId}/versions/${versionId}/rollback`);
      message.success("已回滚，创建了新草稿版本");
      refreshVersions();
      fetchTemplates();
    } catch {
      message.error("回滚失败");
    }
  };

  // ─── DSL 编辑器 ─────────────────────────────────

  const openDSLEditor = (version: PageVersion) => {
    setEditingVersion(version);
    setDslText(JSON.stringify(version.config_json, null, 2));
    setDslErrors([]);
    setDslDrawerOpen(true);
  };

  const handleDSLChange = (value: string) => {
    setDslText(value);
    try {
      const parsed = JSON.parse(value);
      setDslErrors(validateDSL(parsed));
    } catch (e) {
      setDslErrors([`JSON 语法错误: ${(e as Error).message}`]);
    }
  };

  const saveDSL = async () => {
    if (!editingVersion) return;
    try {
      const parsed = JSON.parse(dslText);
      const errors = validateDSL(parsed);
      if (errors.length > 0) {
        message.error("DSL 校验失败，请修复错误");
        return;
      }
      setSaving(true);
      await api.patch(`/page-versions/${editingVersion.id}`, {
        config_json: parsed,
      });
      message.success("DSL 保存成功");
      setDslDrawerOpen(false);
      refreshVersions();
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  // ─── 创建新草稿版本 ────────────────────────────

  const createNewDraft = async () => {
    if (!currentTemplate) return;
    try {
      // 基于最新的已发布版本创建草稿
      const baseConfig = versions.find((v) => v.status === "published")?.config_json
        ?? versions[0]?.config_json
        ?? createEmptyDSL();
      await api.post(`/page-templates/${currentTemplate.id}/versions`, {
        config_json: baseConfig,
      });
      message.success("新草稿版本已创建");
      refreshVersions();
    } catch {
      message.error("创建草稿失败");
    }
  };

  // ─── 列定义 ─────────────────────────────────────

  const columns: ColumnsType<PageTemplate> = [
    { title: "模板名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "template_type",
      key: "template_type",
      render: (t: string) => TYPE_LABELS[t] || t,
    },
    {
      title: "已发布版本",
      key: "published",
      render: (_: unknown, record: PageTemplate) =>
        record.published_version ? (
          <Tag color="blue">v{record.published_version.version}</Tag>
        ) : (
          <Tag>未发布</Tag>
        ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: PageTemplate) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => showVersions(record)}>
            版本管理
          </Button>
          <Button
            size="small"
            href={`/api/v1/page-templates/${record.id}/preview`}
            target="_blank"
          >
            预览
          </Button>
        </Space>
      ),
    },
  ];

  const versionColumns: ColumnsType<PageVersion> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: number) => `v${v}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = VERSION_STATUS_MAP[s] || { label: s, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "发布时间",
      dataIndex: "published_at",
      key: "published_at",
      render: (v: string) => v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "-",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: PageVersion) => (
        <Space>
          {record.status === "draft" && (
            <>
              <Tooltip title="编辑 DSL">
                <Button
                  size="small"
                  icon={<EditOutlined />}
                  onClick={() => openDSLEditor(record)}
                >
                  编辑
                </Button>
              </Tooltip>
              <Popconfirm title="确认发布此版本？" onConfirm={() => publishVersion(record.id)}>
                <Button size="small" type="primary" icon={<SendOutlined />}>
                  发布
                </Button>
              </Popconfirm>
            </>
          )}
          {record.status === "published" && (
            <Popconfirm title="确认下线？" onConfirm={() => archiveVersion(record.id)}>
              <Button size="small" danger icon={<StopOutlined />}>
                下线
              </Button>
            </Popconfirm>
          )}
          {record.status !== "draft" && currentTemplate && (
            <Tooltip title="基于此版本创建新草稿">
              <Button
                size="small"
                icon={<RollbackOutlined />}
                onClick={() => rollbackVersion(currentTemplate.id, record.id)}
              >
                回滚
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  // ─── 渲染 ───────────────────────────────────────

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">页面管理</Title>
        <Space>
          <Button onClick={() => { setIndustryOpen(true); fetchIndustryTemplates(); }}>
            行业模板库
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建页面
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={templates}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page, total, pageSize: 20, onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />

      {/* 创建模板 */}
      <Modal
        title="新建页面模板"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="模板名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="template_type" label="类型" rules={[{ required: true }]}>
            <Select options={Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 行业模板库 */}
      <Modal
        title="行业模板库"
        open={industryOpen}
        onCancel={() => setIndustryOpen(false)}
        footer={null}
        width={700}
      >
        <div className="grid grid-cols-1 gap-4">
          {industryTemplates.map((tpl, i) => (
            <div key={i} className="flex items-center justify-between rounded border p-4">
              <div>
                <div className="font-medium">{String(tpl.name)}</div>
                <div className="text-sm text-gray-500">{String(tpl.description)}</div>
              </div>
              <Button type="primary" onClick={() => cloneTemplate(i)}>
                使用模板
              </Button>
            </div>
          ))}
        </div>
      </Modal>

      {/* 版本管理 */}
      <Modal
        title={`版本管理 — ${currentTemplate?.name || ""}`}
        open={versionsOpen}
        onCancel={() => setVersionsOpen(false)}
        footer={
          <Button type="primary" onClick={createNewDraft}>
            创建新草稿
          </Button>
        }
        width={800}
      >
        <Table
          columns={versionColumns}
          dataSource={versions}
          rowKey="id"
          pagination={false}
          size="small"
        />
      </Modal>

      {/* DSL 编辑器 Drawer */}
      <Drawer
        title={
          <Space>
            <CodeOutlined />
            <span>页面 DSL 编辑</span>
            {editingVersion && (
              <Tag color="blue">v{editingVersion.version}</Tag>
            )}
          </Space>
        }
        open={dslDrawerOpen}
        onClose={() => setDslDrawerOpen(false)}
        width={720}
        extra={
          <Space>
            <Button onClick={() => setDslDrawerOpen(false)}>取消</Button>
            <Button
              type="primary"
              onClick={saveDSL}
              loading={saving}
              disabled={dslErrors.length > 0}
              icon={<CheckCircleOutlined />}
            >
              保存
            </Button>
          </Space>
        }
      >
        <Tabs
          defaultActiveKey="json"
          items={[
            {
              key: "json",
              label: "JSON 编辑",
              children: (
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <Text type="secondary">直接编辑 JSON 配置</Text>
                    {dslErrors.length > 0 ? (
                      <Tag color="red">{dslErrors.length} 个错误</Tag>
                    ) : dslText ? (
                      <Tag color="green">校验通过</Tag>
                    ) : null}
                  </div>
                  <TextArea
                    value={dslText}
                    onChange={(e) => handleDSLChange(e.target.value)}
                    rows={20}
                    className="font-mono text-sm"
                    placeholder="输入 JSON DSL 配置..."
                  />
                  {dslErrors.length > 0 && (
                    <div className="mt-2 rounded bg-red-50 p-3">
                      {dslErrors.map((err, i) => (
                        <div key={i} className="text-sm text-red-600">
                          {err}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: "visual",
              label: "模块配置",
              children: (
                <ModuleEditor
                  dslText={dslText}
                  onChange={(newDSL) => {
                    setDslText(JSON.stringify(newDSL, null, 2));
                    setDslErrors(validateDSL(newDSL));
                  }}
                />
              ),
            },
            {
              key: "routing",
              label: "活动期配置",
              children: (
                <RoutingEditor
                  dslText={dslText}
                  onChange={(newDSL) => {
                    setDslText(JSON.stringify(newDSL, null, 2));
                    setDslErrors(validateDSL(newDSL));
                  }}
                />
              ),
            },
          ]}
        />
      </Drawer>
    </div>
  );
}

/* ─── 模块可视化编辑器 ─────────────────────────── */

function ModuleEditor({
  dslText,
  onChange,
}: {
  dslText: string;
  onChange: (dsl: PageDSL) => void;
}) {
  const [modules, setModules] = useState<ModuleConfig[]>([]);

  useEffect(() => {
    try {
      const dsl = JSON.parse(dslText) as PageDSL;
      setModules(Array.isArray(dsl.modules) ? dsl.modules : []);
    } catch {
      setModules([]);
    }
  }, [dslText]);

  const updateDSL = (newModules: ModuleConfig[]) => {
    setModules(newModules);
    try {
      const dsl = JSON.parse(dslText) as PageDSL;
      onChange({ ...dsl, modules: newModules });
    } catch {
      onChange({ modules: newModules });
    }
  };

  const addModule = () => {
    const id = `mod_${Date.now()}`;
    const newMod: ModuleConfig = { id, type: "product_hero", enabled: true, config: {} };
    updateDSL([newMod, ...modules]);
  };

  const updateModule = (index: number, updates: Partial<ModuleConfig>) => {
    const newModules = modules.map((m, i) => (i === index ? { ...m, ...updates } : m));
    updateDSL(newModules);
  };

  const removeModule = (index: number) => {
    updateDSL(modules.filter((_, i) => i !== index));
  };

  const moveUp = (index: number) => {
    if (index === 0) return;
    const newModules = [...modules];
    [newModules[index - 1], newModules[index]] = [newModules[index], newModules[index - 1]];
    updateDSL(newModules);
  };

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="small" icon={<PlusOutlined />} onClick={addModule}>
          添加模块
        </Button>
      </div>
      <div className="space-y-3">
        {modules.map((mod, i) => (
          <div key={mod.id} className="rounded border p-3">
            <div className="flex items-center gap-2">
              <Switch
                size="small"
                checked={mod.enabled}
                onChange={(checked) => updateModule(i, { enabled: checked })}
              />
              <Select
                size="small"
                value={mod.type}
                onChange={(type: ModuleType) => updateModule(i, { type })}
                options={MODULE_TYPES}
                style={{ width: 140 }}
              />
              <Input
                size="small"
                value={mod.id}
                onChange={(e) => updateModule(i, { id: e.target.value })}
                style={{ width: 120 }}
                placeholder="模块 ID"
              />
              <div className="flex-1" />
              <Button size="small" disabled={i === 0} onClick={() => moveUp(i)}>
                ↑
              </Button>
              <Popconfirm title="删除此模块？" onConfirm={() => removeModule(i)}>
                <Button size="small" danger>删除</Button>
              </Popconfirm>
            </div>
            <div className="mt-2 rounded bg-gray-50 p-2">
              <ModuleConfigForm
                moduleType={mod.type}
                config={mod.config || {}}
                onChange={(config) => updateModule(i, { config })}
              />
            </div>
          </div>
        ))}
        {modules.length === 0 && (
          <div className="py-8 text-center text-gray-400">
            暂无模块，点击"添加模块"开始
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── 活动期路由配置 ───────────────────────────── */

function RoutingEditor({
  dslText,
  onChange,
}: {
  dslText: string;
  onChange: (dsl: PageDSL) => void;
}) {
  const [routing, setRouting] = useState<PageDSL["routing"]>();

  useEffect(() => {
    try {
      const dsl = JSON.parse(dslText) as PageDSL;
      setRouting(dsl.routing);
    } catch { /* ignore */ }
  }, [dslText]);

  const updateDSL = (newRouting: PageDSL["routing"]) => {
    setRouting(newRouting);
    try {
      const dsl = JSON.parse(dslText) as PageDSL;
      onChange({ ...dsl, routing: newRouting });
    } catch {
      onChange({ routing: newRouting });
    }
  };

  const periods = routing?.campaign_periods ?? [];

  const addPeriod = () => {
    const newPeriods = [{ campaign_id: "", start_at: "", end_at: "", mode: "campaign" as const }, ...periods];
    updateDSL({ ...routing, campaign_periods: newPeriods });
  };

  const updatePeriod = (index: number, updates: Record<string, unknown>) => {
    const newPeriods = periods.map((p, i) => (i === index ? { ...p, ...updates } : p));
    updateDSL({ ...routing, campaign_periods: newPeriods });
  };

  const removePeriod = (index: number) => {
    updateDSL({ ...routing, campaign_periods: periods.filter((_, i) => i !== index) });
  };

  return (
    <div>
      <div className="mb-3">
        <div className="mb-2 flex items-center gap-2">
          <Switch
            size="small"
            checked={routing?.default_page ?? true}
            onChange={(checked) => updateDSL({ ...routing, default_page: checked })}
          />
          <Text>设为默认页面</Text>
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <Text strong>活动期配置</Text>
        <Button size="small" icon={<PlusOutlined />} onClick={addPeriod}>
          添加活动期
        </Button>
      </div>

      <div className="space-y-3">
        {periods.map((period, i) => (
          <div key={i} className="rounded border p-3">
            <div className="flex items-center gap-2 mb-2">
              <Tag color={period.mode === "evergreen" ? "green" : "blue"}>
                {period.mode === "evergreen" ? "常驻" : "活动期"}
              </Tag>
              <Select
                size="small"
                value={period.mode}
                onChange={(mode) => updatePeriod(i, { mode })}
                options={[
                  { value: "evergreen", label: "常驻（非活动期）" },
                  { value: "campaign", label: "活动期" },
                ]}
                style={{ width: 160 }}
              />
              <div className="flex-1" />
              <Button size="small" danger onClick={() => removePeriod(i)}>
                删除
              </Button>
            </div>
            {period.mode === "campaign" && (
              <div className="space-y-2">
                <Input
                  size="small"
                  placeholder="活动 ID（可选）"
                  value={period.campaign_id || ""}
                  onChange={(e) => updatePeriod(i, { campaign_id: e.target.value })}
                />
                <Space>
                  <DatePicker
                    size="small"
                    placeholder="开始时间"
                    value={period.start_at ? dayjs(period.start_at) : undefined}
                    onChange={(d) => updatePeriod(i, { start_at: d?.toISOString() || "" })}
                  />
                  <DatePicker
                    size="small"
                    placeholder="结束时间"
                    value={period.end_at ? dayjs(period.end_at) : undefined}
                    onChange={(d) => updatePeriod(i, { end_at: d?.toISOString() || "" })}
                  />
                </Space>
              </div>
            )}
          </div>
        ))}
        {periods.length === 0 && (
          <div className="py-8 text-center text-gray-400">
            暂无活动期配置
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── 模块配置表单 ────────────────────────────── */

function ModuleConfigForm({
  moduleType,
  config,
  onChange,
}: {
  moduleType: string;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const update = (key: string, value: unknown) => {
    onChange({ ...config, [key]: value });
  };

  switch (moduleType) {
    /* ─── 产品展示 ────────────────────────────── */
    case "product_hero":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_verify_badge} onChange={(v) => update("show_verify_badge", v)} />
            <Text type="secondary" className="text-xs">显示验真徽章</Text>
          </div>
          <Input size="small" placeholder="产品图片 URL（可选，留空使用产品数据）" value={String(config.image_url || "")} onChange={(e) => update("image_url", e.target.value)} />
          <Input size="small" placeholder="标题模板，如 {{product.name}}" value={String(config.title_template || "")} onChange={(e) => update("title_template", e.target.value)} />
        </div>
      );

    /* ─── 验真状态 ────────────────────────────── */
    case "verification_status":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="首次扫码提示语" value={String(config.first_scan_text || "")} onChange={(e) => update("first_scan_text", e.target.value)} />
          <Input size="small" placeholder="重复扫码提示语" value={String(config.repeat_scan_text || "")} onChange={(e) => update("repeat_scan_text", e.target.value)} />
          <Input size="small" placeholder="无效码提示语" value={String(config.invalid_text || "")} onChange={(e) => update("invalid_text", e.target.value)} />
        </div>
      );

    /* ─── 溯源信息 ────────────────────────────── */
    case "light_traceability": {
      const allFields = [
        { value: "origin", label: "产地" },
        { value: "production_date", label: "生产日期" },
        { value: "expiry_date", label: "保质期至" },
        { value: "batch_no", label: "批次号" },
      ];
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">显示字段</Text>
          <Select
            mode="multiple"
            size="small"
            placeholder="选择要显示的溯源字段"
            value={(config.fields as string[]) || []}
            onChange={(v) => update("fields", v)}
            options={allFields}
            style={{ width: "100%" }}
          />
        </div>
      );
    }

    /* ─── 检测报告 ────────────────────────────── */
    case "test_reports":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">报告 ID 列表</Text>
          <Select
            mode="tags"
            size="small"
            placeholder="输入报告 ID 后回车添加"
            value={((config.report_ids as string[]) || []).map(String)}
            onChange={(v) => update("report_ids", v)}
            style={{ width: "100%" }}
            open={false}
          />
        </div>
      );

    /* ─── 资质证书 ────────────────────────────── */
    case "certificates":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">证书配置（JSON 数组，每个含 name/issuer/valid_until/image_url/file_url）</Text>
          <TextArea
            size="small"
            rows={3}
            value={JSON.stringify(config.certificates || [], null, 0)}
            onChange={(e) => {
              try { update("certificates", JSON.parse(e.target.value)); } catch { /* ignore */ }
            }}
          />
        </div>
      );

    /* ─── 权益卡片 ────────────────────────────── */
    case "benefit_card":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="权益 ID" value={String(config.benefit_id || "")} onChange={(e) => update("benefit_id", e.target.value)} />
          <Select size="small" placeholder="权益类型" value={config.benefit_type || undefined} onChange={(v) => update("benefit_type", v)}
            options={[{ value: "coupon", label: "优惠券" }, { value: "points", label: "积分" }, { value: "lottery", label: "抽奖" }, { value: "gift", label: "礼品" }]}
            style={{ width: 120 }} />
          <Input size="small" placeholder="标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <Input size="small" placeholder="描述" value={String(config.description || "")} onChange={(e) => update("description", e.target.value)} />
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.require_consent} onChange={(v) => update("require_consent", v)} />
            <Text type="secondary" className="text-xs">需要隐私授权</Text>
          </div>
        </div>
      );

    /* ─── 私域/跳转按钮 ───────────────────────── */
    case "cta_group":
      return (
        <div className="space-y-1">
          <Text type="secondary" className="text-xs">按钮配置（JSON）</Text>
          <TextArea size="small" rows={3} value={JSON.stringify(config.buttons || [], null, 0)}
            onChange={(e) => { try { update("buttons", JSON.parse(e.target.value)); } catch { /* ignore */ } }} />
        </div>
      );

    /* ─── 购买渠道 ────────────────────────────── */
    case "shop_redirect": {
      const shops = (config.shops as Array<{ platform: string; name: string; url: string }>) || [];
      const addShop = () => update("shops", [{ platform: "taobao", name: "", url: "" }, ...shops]);
      const updateShop = (i: number, field: string, val: string) => {
        const updated = shops.map((s, idx) => idx === i ? { ...s, [field]: val } : s);
        update("shops", updated);
      };
      const removeShop = (i: number) => update("shops", shops.filter((_, idx) => idx !== i));
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Text type="secondary" className="text-xs">购买渠道列表</Text>
            <Button size="small" icon={<PlusOutlined />} onClick={addShop}>添加渠道</Button>
          </div>
          {shops.map((shop, i) => (
            <div key={i} className="flex items-center gap-1">
              <Select size="small" value={shop.platform} onChange={(v) => updateShop(i, "platform", v)}
                options={[
                  { value: "taobao", label: "淘宝" },
                  { value: "jd", label: "京东" },
                  { value: "douyin", label: "抖音" },
                  { value: "pdd", label: "拼多多" },
                  { value: "other", label: "其他" },
                ]}
                style={{ width: 80 }}
              />
              <Input size="small" placeholder="渠道名称" value={shop.name} onChange={(e) => updateShop(i, "name", e.target.value)} style={{ flex: 1 }} />
              <Input size="small" placeholder="链接" value={shop.url} onChange={(e) => updateShop(i, "url", e.target.value)} style={{ flex: 1 }} />
              <Button size="small" danger onClick={() => removeShop(i)}>×</Button>
            </div>
          ))}
          {shops.length === 0 && <Text type="secondary" className="text-xs">暂未配置渠道</Text>}
        </div>
      );
    }

    /* ─── 留资表单 ────────────────────────────── */
    case "lead_form": {
      const allFormFields = [
        { value: "name", label: "姓名" },
        { value: "phone", label: "手机号" },
        { value: "address", label: "地址" },
        { value: "email", label: "邮箱" },
        { value: "remark", label: "备注" },
      ];
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="表单标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <Input size="small" placeholder="副标题" value={String(config.subtitle || "")} onChange={(e) => update("subtitle", e.target.value)} />
          <Input size="small" placeholder="提交按钮文案" value={String(config.submit_label || "")} onChange={(e) => update("submit_label", e.target.value)} />
          <Text type="secondary" className="text-xs">表单字段</Text>
          <Select
            mode="multiple"
            size="small"
            placeholder="选择需要收集的字段"
            value={(config.fields as string[]) || []}
            onChange={(v) => update("fields", v)}
            options={allFormFields}
            style={{ width: "100%" }}
          />
        </div>
      );
    }

    /* ─── 视频/图文 ────────────────────────────── */
    case "media_section":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">媒体内容配置（JSON 数组，每项含 type/video|image/url/caption）</Text>
          <TextArea
            size="small"
            rows={3}
            value={JSON.stringify(config.items || [], null, 0)}
            onChange={(e) => {
              try { update("items", JSON.parse(e.target.value)); } catch { /* ignore */ }
            }}
          />
        </div>
      );

    /* ─── 法律条款 ────────────────────────────── */
    case "legal_terms":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_privacy_policy} onChange={(v) => update("show_privacy_policy", v)} />
            <Text type="secondary" className="text-xs">显示隐私政策</Text>
          </div>
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.show_campaign_rules} onChange={(v) => update("show_campaign_rules", v)} />
            <Text type="secondary" className="text-xs">显示活动规则</Text>
          </div>
          <Input size="small" placeholder="隐私政策内容（可选，留空使用默认）" value={String(config.privacy_content || "")} onChange={(e) => update("privacy_content", e.target.value)} />
        </div>
      );

    /* ─── 自定义 HTML ────────────────────────────── */
    case "custom_html":
      return (
        <div className="space-y-2">
          <Text type="secondary" className="text-xs">自定义 HTML 内容</Text>
          <TextArea
            size="small"
            rows={5}
            value={String(config.html || "")}
            onChange={(e) => update("html", e.target.value)}
            placeholder="<div>...</div>"
            className="font-mono text-xs"
          />
        </div>
      );

    /* ─── 会员卡片 ────────────────────────────── */
    case "member_card":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="消费者 ID" value={String(config.consumer_id || "")} onChange={(e) => update("consumer_id", e.target.value)} />
          <Select size="small" placeholder="会员等级" value={config.member_level || undefined} onChange={(v) => update("member_level", v)}
            options={[{ value: "bronze", label: "青铜" }, { value: "silver", label: "白银" }, { value: "gold", label: "黄金" }, { value: "diamond", label: "钻石" }]}
            style={{ width: 120 }} />
        </div>
      );

    /* ─── 积分余额 ────────────────────────────── */
    case "points_balance":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="消费者 ID（可选）" value={String(config.consumer_id || "")} onChange={(e) => update("consumer_id", e.target.value)} />
          <InputNumber size="small" placeholder="初始积分" min={0} value={Number(config.points) || undefined} onChange={(v) => update("points", v)} />
        </div>
      );

    /* ─── 积分兑换 ────────────────────────────── */
    case "points_exchange":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="权益 ID" value={String(config.benefit_id || "")} onChange={(e) => update("benefit_id", e.target.value)} />
          <Input size="small" placeholder="标题" value={String(config.title || "")} onChange={(e) => update("title", e.target.value)} />
          <InputNumber size="small" placeholder="所需积分" min={0} value={Number(config.points_cost) || undefined} onChange={(v) => update("points_cost", v)} />
          <Input size="small" placeholder="描述" value={String(config.description || "")} onChange={(e) => update("description", e.target.value)} />
        </div>
      );

    /* ─── 外码引导 ────────────────────────────── */
    case "outer_code_guide":
      return (
        <div className="space-y-2">
          <Input size="small" placeholder="品牌名称" value={String(config.brand_name || "")} onChange={(e) => update("brand_name", e.target.value)} />
          <Input size="small" placeholder="产品名称" value={String(config.product_name || "")} onChange={(e) => update("product_name", e.target.value)} />
          <Input size="small" placeholder="内码提示文案" value={String(config.inner_code_hint || "")} onChange={(e) => update("inner_code_hint", e.target.value)} />
          <Input size="small" placeholder="产品图片 URL" value={String(config.product_image || "")} onChange={(e) => update("product_image", e.target.value)} />
        </div>
      );

    /* ─── 风险预警 ────────────────────────────── */
    case "risk_alert":
      return (
        <div className="space-y-2">
          <Select size="small" placeholder="预警类型" value={config.alert_type || undefined} onChange={(v) => update("alert_type", v)}
            options={[{ value: "frequency", label: "频率限制" }, { value: "multi_location", label: "多地扫码" }, { value: "suspected_copy", label: "疑似复制码" }]}
            style={{ width: 140 }} />
          <Input size="small" placeholder="详情" value={String(config.detail || "")} onChange={(e) => update("detail", e.target.value)} />
        </div>
      );

    /* ─── 双码验真 ────────────────────────────── */
    case "dual_code_verify":
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Switch size="small" checked={!!config.product_verified} onChange={(v) => update("product_verified", v)} />
            <Text type="secondary" className="text-xs">标记产品已验证</Text>
          </div>
        </div>
      );

    default:
      return (
        <Text type="secondary" className="text-xs italic">
          此模块无可配置项
        </Text>
      );
  }
}
