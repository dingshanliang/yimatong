"use client";

import { useEffect, useState } from "react";
import {
  App,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { STATUS_COLORS } from "@/lib/status-colors";

const LOCALES = [
  { value: "zh", label: "中文 (zh)" },
  { value: "en", label: "English (en)" },
  { value: "ja", label: "日本語 (ja)" },
  { value: "ko", label: "한국어 (ko)" },
];

export default function I18nPage() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [filterLocale, setFilterLocale] = useState<string | undefined>();
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const { t } = useI18n();
  // 与后端 require_permission("tenant:manage") 一致：仅管理员可写。
  const role = useAuthStore((state) => state.user?.role);
  const canManage = role === "admin";

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/i18n/translations", {
        params: filterLocale ? { locale: filterLocale } : {},
      });
      setItems(Array.isArray(data) ? data : []);
    } catch {
      message.error("加载翻译失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetch();
  }, [filterLocale]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      await api.post("/i18n/translations", values);
      message.success("翻译创建成功");
      setOpen(false);
      form.resetFields();
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "创建失败");
    }
  };

  const handleBatch = async () => {
    const sample = [
      { key: "common.hello", locale: "zh", value: "你好" },
      { key: "common.hello", locale: "en", value: "Hello" },
    ];
    try {
      const { data } = await api.post("/i18n/translations/batch", {
        translations: sample,
      });
      message.success(`批量导入 ${data.updated} 条`);
      fetch();
    } catch {
      message.error("批量导入失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: t("i18n.key"), dataIndex: "key", key: "key", width: 250 },
    {
      title: t("i18n.locale"),
      dataIndex: "locale",
      key: "locale",
      width: 100,
      render: (v: string) => <Tag color={STATUS_COLORS.processing}>{v}</Tag>,
    },
    { title: t("i18n.value"), dataIndex: "value", key: "value" },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Space>
          <Select
            allowClear
            placeholder="筛选语言"
            value={filterLocale}
            onChange={setFilterLocale}
            style={{ width: 150 }}
            options={LOCALES}
          />
        </Space>
        <Space>
          {canManage ? (
            <>
              <Button onClick={handleBatch}>{t("i18n.batch_import")}</Button>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setOpen(true)}
              >
                {t("i18n.new_translation")}
              </Button>
            </>
          ) : (
            <span className="text-sm text-text-muted">
              仅管理员可修改多语言文案
            </span>
          )}
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={items}
        rowKey={(r) => `${r.key}-${r.locale}`}
        loading={loading}
        size="small"
        pagination={{
          pageSize: 50,
          showTotal: (total) => t("common.total").replace("{n}", String(total)),
        }}
      />

      <Modal
        title={t("i18n.new_translation")}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="key"
            label={t("i18n.key")}
            rules={[{ required: true }]}
          >
            <Input placeholder="e.g. common.hello" />
          </Form.Item>
          <Form.Item
            name="locale"
            label={t("i18n.locale")}
            rules={[{ required: true }]}
          >
            <Select options={LOCALES} />
          </Form.Item>
          <Form.Item
            name="value"
            label={t("i18n.value")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
