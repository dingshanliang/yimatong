"use client";

import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Space,
  Modal,
  Form,
  Input,
  Tabs,
  Card,
  Row,
  Col,
  Statistic,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { UploadOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title } = Typography;

/* ---------- Dashboard Tab ---------- */

function DashboardTab() {
  const [data, setData] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data: d } = await api.get("/gmv/dashboard");
      setData(d || {});
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  return (
    <Row gutter={[16, 16]}>
      <Col span={6}>
        <Card size="small"><Statistic title="总 GMV" value={Number(data.total_gmv) || 0} prefix="¥" loading={loading} /></Card>
      </Col>
      <Col span={6}>
        <Card size="small"><Statistic title="归因订单数" value={Number(data.attributed_orders) || 0} loading={loading} /></Card>
      </Col>
      <Col span={6}>
        <Card size="small"><Statistic title="总订单数" value={Number(data.total_orders) || 0} loading={loading} /></Card>
      </Col>
      <Col span={6}>
        <Card size="small"><Statistic title="归因率" value={Number(data.attribution_rate) || 0} suffix="%" loading={loading} /></Card>
      </Col>
    </Row>
  );
}

/* ---------- Orders Tab ---------- */

function OrdersTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/gmv/orders", { params: { page, page_size: 20 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载订单列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [page]);

  const handleImport = async (values: Record<string, unknown>) => {
    try {
      const orders = values.orders_json
        ? JSON.parse(values.orders_json as string)
        : [];
      await api.post("/gmv/orders/import", { orders });
      message.success("导入成功");
      setImportOpen(false);
      form.resetFields();
      setPage(1);
      fetch();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail || "导入失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "外部订单号", dataIndex: "external_order_id", key: "external_order_id" },
    { title: "金额", dataIndex: "amount", key: "amount", render: (v: number) => `¥${v}` },
    { title: "来源", dataIndex: "source_system", key: "source_system" },
    { title: "匹配状态", dataIndex: "matched", key: "matched", render: (v: boolean) => <Tag color={v ? "green" : "default"}>{v ? "已归因" : "待匹配"}</Tag> },
  ];

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
          导入订单
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={loading}
        pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />
      <Modal title="导入外部订单" open={importOpen} onCancel={() => setImportOpen(false)} onOk={() => form.submit()} width={600}>
        <Form form={form} layout="vertical" onFinish={handleImport}>
          <Form.Item name="orders_json" label="订单数据 (JSON 数组)" rules={[{ required: true }]}>
            <Input.TextArea rows={8} placeholder='[{"external_order_id":"ORD001","amount":99.9,"source_system":"taobao"}]' />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}

/* ---------- Attributions Tab ---------- */

function AttributionsTab() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);

  const fetch = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/gmv/attributions", { params: { page, page_size: 20 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载归因数据失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, [page]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "订单 ID", dataIndex: "order_id", key: "order_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "消费者 ID", dataIndex: "consumer_id", key: "consumer_id", render: (v: string) => v?.slice(0, 8) + "..." },
    { title: "匹配方式", dataIndex: "match_method", key: "match_method" },
    { title: "金额", dataIndex: "amount", key: "amount", render: (v: number) => `¥${v}` },
  ];

  return (
    <Table
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
    />
  );
}

/* ---------- Main ---------- */

const tabItems = [
  { key: "dashboard", label: "GMV 看板", children: <DashboardTab /> },
  { key: "orders", label: "外部订单", children: <OrdersTab /> },
  { key: "attributions", label: "归因记录", children: <AttributionsTab /> },
];

export default function GmvPage() {
  return (
    <div>
      <Title level={4} className="!mb-4">GMV 归因</Title>
      <Tabs defaultActiveKey="dashboard" items={tabItems} />
    </div>
  );
}
