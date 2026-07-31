"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  SendOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useCrud } from "@/lib/hooks";
import { createDefaultModules, createEmptyDSL } from "@/lib/page-dsl";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Text, Title } = Typography;

type StartMode = "blank" | "template";

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json?: Record<string, unknown>;
  published_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
  description?: string;
  product_id?: string | null;
  product_name?: string | null;
  display_status?: "unpublished" | "published" | "has_unpublished_draft";
  published_version?: PageVersion | null;
  draft_version?: PageVersion | null;
  updated_at?: string | null;
}

interface ProductOption {
  id: string;
  name: string;
}

interface IndustryTemplate {
  name: string;
  template_type: string;
  description?: string;
}

interface CreatePageValues {
  name: string;
  template_type: string;
  product_id?: string;
  start_mode: StartMode;
  template_index?: number;
}

const TYPE_LABELS: Record<string, string> = {
  product_info: "产品信息",
  traceability: "溯源页",
  brand_story: "品牌故事",
  campaign: "活动页",
};

const TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}));

const STATUS_META: Record<string, { label: string; color: string }> = {
  unpublished: { label: "未发布", color: STATUS_COLORS.neutral },
  published: { label: "已发布", color: STATUS_COLORS.success },
  has_unpublished_draft: {
    label: "有未发布草稿",
    color: STATUS_COLORS.warning,
  },
};

function buildDefaultDSL() {
  const initialDSL = createEmptyDSL();
  initialDSL.modules = createDefaultModules();
  return initialDSL;
}

function getDisplayStatus(record: PageTemplate) {
  if (record.display_status) return record.display_status;
  if (record.published_version && record.draft_version)
    return "has_unpublished_draft";
  if (record.published_version) return "published";
  return "unpublished";
}

function getPreviewUrl(templateId: string) {
  return `${api.defaults.baseURL}/page-templates/${templateId}/preview`;
}

export default function PagesPage() {
  const router = useRouter();
  const { message, modal } = App.useApp();
  const [form] = Form.useForm<CreatePageValues>();
  const startMode = Form.useWatch("start_mode", form);

  const {
    items: templates,
    total,
    page,
    loading,
    setPage,
    mutate: mutateTemplates,
  } = useCrud<PageTemplate>("/page-templates");

  const [createOpen, setCreateOpen] = useState(false);
  const [products, setProducts] = useState<ProductOption[]>([]);
  const [industryTemplates, setIndustryTemplates] = useState<
    IndustryTemplate[]
  >([]);
  const [submitting, setSubmitting] = useState(false);

  const productOptions = useMemo(
    () =>
      products.map((product) => ({ value: product.id, label: product.name })),
    [products]
  );

  const fetchProducts = useCallback(async () => {
    try {
      const { data } = await api.get("/products", {
        params: { page_size: 100 },
      });
      setProducts(data.items || []);
    } catch {
      setProducts([]);
    }
  }, []);

  const fetchIndustryTemplates = useCallback(async () => {
    try {
      const { data } = await api.get("/page-templates/industry-templates");
      setIndustryTemplates(data || []);
    } catch {
      setIndustryTemplates([]);
    }
  }, []);

  useEffect(() => {
    void fetchProducts();
  }, [fetchProducts]);

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({ start_mode: "blank", template_type: "traceability" });
    setCreateOpen(true);
    void fetchProducts();
    void fetchIndustryTemplates();
  };

  const handleTemplatePick = (index: number) => {
    const tpl = industryTemplates[index];
    if (!tpl) return;
    form.setFieldsValue({
      template_index: index,
      template_type: tpl.template_type,
      name: form.getFieldValue("name") || tpl.name,
    });
  };

  const handleCreate = async (values: CreatePageValues) => {
    try {
      setSubmitting(true);
      let templateId = "";
      if (values.start_mode === "template") {
        if (values.template_index === undefined) {
          message.error("请选择一个行业模板");
          return;
        }
        const { data } = await api.post(
          `/page-templates/industry-templates/${values.template_index}/clone`,
          {
            name: values.name,
            product_id: values.product_id || null,
          }
        );
        templateId = data.template.id;
      } else {
        const { data: template } = await api.post("/page-templates", {
          name: values.name,
          template_type: values.template_type,
          product_id: values.product_id || null,
        });
        templateId = template.id;
        await api.post(`/page-templates/${templateId}/versions`, {
          config_json: buildDefaultDSL(),
        });
      }
      message.success("页面已创建，继续编辑草稿");
      setCreateOpen(false);
      form.resetFields();
      void mutateTemplates();
      router.push(`/pages/${templateId}/edit`);
    } catch {
      message.error("创建页面失败");
    } finally {
      setSubmitting(false);
    }
  };

  const ensureDraftAndEdit = async (record: PageTemplate) => {
    if (record.draft_version) {
      router.push(`/pages/${record.id}/edit`);
      return;
    }
    try {
      const config = record.published_version?.config_json ?? buildDefaultDSL();
      await api.post(`/page-templates/${record.id}/versions`, {
        config_json: config,
      });
      message.success("已基于当前页面创建草稿");
      void mutateTemplates();
      router.push(`/pages/${record.id}/edit`);
    } catch {
      message.error("创建草稿失败");
    }
  };

  const publishDraft = (record: PageTemplate) => {
    const draft = record.draft_version;
    if (!draft) {
      message.warning("当前页面没有可发布的草稿");
      return;
    }
    modal.confirm({
      title: "发布此页面草稿？",
      okText: "发布草稿",
      cancelText: "取消",
      icon: <SendOutlined />,
      content: (
        <div className="mt-3">
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="页面">{record.name}</Descriptions.Item>
            <Descriptions.Item label="关联产品">
              {record.product_name || "未关联产品"}
            </Descriptions.Item>
            <Descriptions.Item label="草稿版本">
              v{draft.version}
            </Descriptions.Item>
          </Descriptions>
          <Text type="secondary" className="mt-3 block">
            发布后，关联产品的消费者扫码页可能展示此页面内容。
          </Text>
        </div>
      ),
      async onOk() {
        try {
          await api.post(`/page-versions/${draft.id}/publish`);
          message.success("页面草稿已发布");
          await mutateTemplates();
        } catch {
          message.error("发布失败");
        }
      },
    });
  };

  const columns: ColumnsType<PageTemplate> = [
    {
      title: "页面名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <div>
          <Button
            type="link"
            className="h-auto p-0"
            onClick={() => ensureDraftAndEdit(record)}
          >
            {name}
          </Button>
          {record.description && (
            <div
              className="mt-1 text-xs"
              style={{ color: "var(--ymt-color-text-tertiary)" }}
            >
              {record.description}
            </div>
          )}
        </div>
      ),
    },
    {
      title: "页面类型",
      dataIndex: "template_type",
      key: "template_type",
      render: (type: string) => TYPE_LABELS[type] || type,
    },
    {
      title: "关联产品",
      key: "product",
      render: (_, record) =>
        record.product_name ? (
          record.product_name
        ) : (
          <Tooltip title="未关联产品时，消费者扫码不会自动命中该页面">
            <Tag color={STATUS_COLORS.warning}>未关联产品</Tag>
          </Tooltip>
        ),
    },
    {
      title: "发布状态",
      key: "display_status",
      render: (_, record) => {
        const status = getDisplayStatus(record);
        const meta = STATUS_META[status];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "版本",
      key: "versions",
      render: (_, record) => {
        if (!record.published_version && !record.draft_version)
          return <Text type="secondary">暂无版本</Text>;
        return (
          <Space size={4} wrap>
            {record.published_version && (
              <Tag color={STATUS_COLORS.processing}>
                已发布 v{record.published_version.version}
              </Tag>
            )}
            {record.draft_version && (
              <Tag>草稿 v{record.draft_version.version}</Tag>
            )}
          </Space>
        );
      },
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      render: (_, record) => {
        const status = getDisplayStatus(record);
        return (
          <Space size={8} wrap>
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => ensureDraftAndEdit(record)}
            >
              编辑草稿
            </Button>
            {record.draft_version && (
              <Button
                size="small"
                icon={<EyeOutlined />}
                onClick={() => router.push(`/pages/${record.id}/edit`)}
              >
                预览草稿
              </Button>
            )}
            {record.published_version && (
              <Button
                size="small"
                icon={<EyeOutlined />}
                href={getPreviewUrl(record.id)}
                target="_blank"
              >
                预览线上页
              </Button>
            )}
            {record.draft_version && (
              <Button
                size="small"
                type={status === "published" ? "default" : "primary"}
                onClick={() => publishDraft(record)}
              >
                {status === "has_unpublished_draft" ? "发布草稿" : "发布"}
              </Button>
            )}
            <Button
              size="small"
              icon={<UnorderedListOutlined />}
              onClick={() => router.push(`/pages/${record.id}`)}
            >
              版本记录
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            页面管理
          </Title>
          <Text type="secondary">管理消费者扫码后看到的 H5 页面</Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建页面
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={templates}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (count) => `共 ${count} 条`,
        }}
      />

      <Modal
        title="新建页面"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        okText="创建并编辑草稿"
        confirmLoading={submitting}
        width={720}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="页面名称"
            rules={[{ required: true, message: "请输入页面名称" }]}
          >
            <Input placeholder="例如 五常稻花香扫码信任页" />
          </Form.Item>
          <Form.Item
            name="template_type"
            label="页面类型"
            rules={[{ required: true, message: "请选择页面类型" }]}
          >
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="product_id"
            label="关联产品"
            extra="关联后，消费者扫码该产品的码时可自动命中此页面。"
          >
            <Select
              allowClear
              showSearch
              placeholder="选择产品"
              optionFilterProp="label"
              options={productOptions}
            />
          </Form.Item>
          <Form.Item
            name="start_mode"
            label="起始内容"
            rules={[{ required: true }]}
          >
            <Radio.Group>
              <Radio.Button value="blank">从空白页开始</Radio.Button>
              <Radio.Button value="template">从行业模板开始</Radio.Button>
            </Radio.Group>
          </Form.Item>
          {startMode === "template" && (
            <Form.Item
              name="template_index"
              label="选择行业模板"
              rules={[{ required: true, message: "请选择行业模板" }]}
            >
              <Radio.Group className="grid w-full grid-cols-1 gap-3">
                {industryTemplates.map((tpl, index) => (
                  <Radio
                    key={`${tpl.name}-${index}`}
                    value={index}
                    onChange={() => handleTemplatePick(index)}
                  >
                    <div className="pl-1">
                      <div className="font-medium">{tpl.name}</div>
                      <div
                        className="text-xs"
                        style={{ color: "var(--ymt-color-text-tertiary)" }}
                      >
                        {TYPE_LABELS[tpl.template_type] || tpl.template_type} ·{" "}
                        {tpl.description}
                      </div>
                    </div>
                  </Radio>
                ))}
              </Radio.Group>
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
