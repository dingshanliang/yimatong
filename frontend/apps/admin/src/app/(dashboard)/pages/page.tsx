"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table, Button, Space, Modal, Form, Input, Select, Tag, Typography,
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
            {mod.config && Object.keys(mod.config).length > 0 && (
              <div className="mt-2 rounded bg-gray-50 p-2">
                <Text type="secondary" className="text-xs">
                  配置: {JSON.stringify(mod.config)}
                </Text>
              </div>
            )}
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
