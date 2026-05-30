"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useCrud } from "@/lib/hooks";
import { App, Button, Form, Input, Modal, Select, Space, Table, Tag } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { createEmptyDSL, createDefaultModules } from "@/lib/page-dsl";

const { TextArea } = Input;

interface PageTemplate {
  id: string;
  name: string;
  template_type: string;
  status: string;
  description?: string;
  published_version?: { version: number } | null;
}

const TYPE_LABELS: Record<string, string> = {
  product_info: "产品信息",
  traceability: "溯源页",
  brand_story: "品牌故事",
  campaign: "活动页",
};

export default function PagesPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const {
    items: templates,
    total,
    page,
    loading,
    setPage,
    mutate: mutateTemplates,
  } = useCrud<PageTemplate>("/page-templates");

  const [createOpen, setCreateOpen] = useState(false);
  const [industryOpen, setIndustryOpen] = useState(false);
  const [industryTemplates, setIndustryTemplates] = useState<
    Record<string, unknown>[]
  >([]);
  const [form] = Form.useForm();

  const handleCreate = async (values: Record<string, string>) => {
    try {
      const { data: tpl } = await api.post("/page-templates", values);
      const initialDSL = createEmptyDSL();
      initialDSL.modules = createDefaultModules();
      await api.post(`/page-templates/${tpl.id}/versions`, {
        config_json: initialDSL,
      });
      message.success("页面模板创建成功，已生成初始草稿");
      setCreateOpen(false);
      form.resetFields();
      mutateTemplates();
    } catch {
      message.error("创建失败");
    }
  };

  const fetchIndustryTemplates = async () => {
    try {
      const { data } = await api.get("/page-templates/industry-templates");
      setIndustryTemplates(data);
    } catch {
      /* ignore */
    }
  };

  const cloneTemplate = async (index: number) => {
    try {
      await api.post(`/page-templates/industry-templates/${index}/clone`);
      message.success("模板复制成功");
      setIndustryOpen(false);
      mutateTemplates();
    } catch {
      message.error("复制失败");
    }
  };

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
          <Button
            size="small"
            onClick={() => router.push(`/pages/${record.id}`)}
          >
            版本管理
          </Button>
          <Button
            size="small"
            type="primary"
            onClick={() => router.push(`/pages/${record.id}/edit`)}
          >
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h4 className="!mb-0 text-lg font-semibold">页面管理</h4>
        <Space>
          <Button
            onClick={() => {
              setIndustryOpen(true);
              fetchIndustryTemplates();
            }}
          >
            行业模板库
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
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
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
      />

      <Modal
        title="新建页面模板"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label="模板名称"
            rules={[{ required: true }]}
          >
            <Input data-testid="page-template-name-input" />
          </Form.Item>
          <Form.Item
            name="template_type"
            label="类型"
            rules={[{ required: true }]}
          >
            <Select
              data-testid="page-template-type-select"
              options={Object.entries(TYPE_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="行业模板库"
        open={industryOpen}
        onCancel={() => setIndustryOpen(false)}
        footer={null}
        width={700}
      >
        <div className="grid grid-cols-1 gap-4">
          {industryTemplates.map((tpl, i) => (
            <div
              key={i}
              className="flex items-center justify-between rounded border p-4"
            >
              <div>
                <div className="font-medium">{String(tpl.name)}</div>
                <div className="text-sm text-gray-500">
                  {String(tpl.description)}
                </div>
              </div>
              <Button type="primary" onClick={() => cloneTemplate(i)}>
                使用模板
              </Button>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
