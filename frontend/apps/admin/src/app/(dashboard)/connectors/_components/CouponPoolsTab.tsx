"use client";

import { useState } from "react";
import { App, Button, Form, Input, Modal, Progress, Table, Tag } from "antd";
import { EyeOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useCrud, usePaginatedList } from "@/lib/hooks";
import type { Pool, PoolCode } from "./types";

export function CouponPoolsTab() {
  const { message } = App.useApp();
  const {
    items: pools,
    total: poolsTotal,
    page: poolsPage,
    pageSize: poolsPageSize,
    setPage: setPoolsPage,
    loading: poolsLoading,
    mutate: mutatePools,
  } = useCrud<Pool>("/connectors/coupon-pools");
  const [poolModalOpen, setPoolModalOpen] = useState(false);
  const [poolForm] = Form.useForm();
  const [codeModalOpen, setCodeModalOpen] = useState(false);
  const [selectedPool, setSelectedPool] = useState<Pool | null>(null);

  const {
    items: poolCodes,
    total: poolCodesTotal,
    loading: poolCodesLoading,
    page: poolCodesPage,
    setPage: setPoolCodesPage,
  } = usePaginatedList<PoolCode>(
    async ({ page, page_size }) => {
      if (!selectedPool) return { items: [], total: 0 };
      try {
        const { data } = await api.get(
          `/connectors/coupon-pools/${selectedPool.id}/codes`,
          { params: { page, page_size } }
        );
        return { items: data.items || [], total: data.total || 0 };
      } catch {
        return { items: [], total: 0 };
      }
    },
    [selectedPool]
  );

  const handleCreatePool = async () => {
    try {
      const values = await poolForm.validateFields();
      const codes = (values.codes as string)
        .split("\n")
        .map((c: string) => c.trim())
        .filter(Boolean);
      if (codes.length === 0) {
        message.error("请输入至少一个券码");
        return;
      }
      if (codes.length > 5_000) {
        message.error("单次最多导入 5000 个券码");
        return;
      }
      if (codes.some((code: string) => code.length > 100)) {
        message.error("每个券码最多 100 个字符");
        return;
      }
      if (new Set(codes).size !== codes.length) {
        message.error("券码不能重复");
        return;
      }
      await api.post("/connectors/coupon-pools", { name: values.name, codes });
      message.success(`券码池创建成功，共 ${codes.length} 个券码`);
      setPoolModalOpen(false);
      poolForm.resetFields();
      mutatePools();
    } catch (err) {
      const axiosErr = err as Parameters<typeof extractErrorMessage>[0];
      if (axiosErr && typeof axiosErr === "object" && "response" in axiosErr) {
        message.error(extractErrorMessage(axiosErr, "创建失败"));
      }
    }
  };

  const poolColumns = [
    { title: "池名称", dataIndex: "name", key: "name" },
    { title: "总数", dataIndex: "total_codes", key: "total_codes" },
    {
      title: "剩余",
      key: "remaining",
      render: (_: unknown, record: Pool) => {
        const used = record.total_codes - record.remaining;
        const percent =
          record.total_codes > 0
            ? Math.round((used / record.total_codes) * 100)
            : 0;
        return (
          <div className="min-w-25">
            <div className="mb-1 text-xs text-text-muted">
              {record.remaining} / {record.total_codes}
            </div>
            <Progress
              percent={percent}
              size="small"
              status={record.remaining === 0 ? "exception" : undefined}
            />
          </div>
        );
      },
    },
    {
      title: "状态",
      key: "status",
      render: (_: unknown, record: Pool) => (
        <Tag
          color={
            record.remaining > 0 ? STATUS_COLORS.success : STATUS_COLORS.error
          }
        >
          {record.remaining > 0 ? "有库存" : "已耗尽"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Pool) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => {
            setSelectedPool(record);
            setCodeModalOpen(true);
          }}
        >
          查看码
        </Button>
      ),
    },
  ];

  const poolCodeColumns = [
    { title: "券码", dataIndex: "code", key: "code" },
    {
      title: "消费者",
      dataIndex: "consumer_id",
      key: "consumer_id",
      render: (v: string | null) => v || "-",
    },
    {
      title: "已分配",
      dataIndex: "distributed",
      key: "distributed",
      render: (v: boolean) => (
        <Tag color={v ? STATUS_COLORS.processing : STATUS_COLORS.neutral}>
          {v ? "是" : "否"}
        </Tag>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            poolForm.resetFields();
            setPoolModalOpen(true);
          }}
        >
          创建券码池
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => mutatePools()}
          style={{ marginLeft: 8 }}
        >
          刷新
        </Button>
      </div>
      <Table
        dataSource={pools}
        columns={poolColumns}
        rowKey="id"
        loading={poolsLoading}
        pagination={{
          current: poolsPage,
          total: poolsTotal,
          pageSize: poolsPageSize,
          onChange: setPoolsPage,
          showTotal: (total) => `共 ${total} 个券码池`,
        }}
      />

      <Modal
        title="创建券码池"
        open={poolModalOpen}
        onOk={handleCreatePool}
        onCancel={() => setPoolModalOpen(false)}
        okText="创建"
      >
        <Form form={poolForm} layout="vertical">
          <Form.Item
            name="name"
            label="池名称"
            rules={[
              { required: true, message: "请输入池名称" },
              { max: 200, message: "池名称最多 200 个字符" },
            ]}
          >
            <Input placeholder="如：2026年6月优惠券" />
          </Form.Item>
          <Form.Item
            name="codes"
            label="券码列表"
            rules={[{ required: true, message: "请输入券码" }]}
            extra="每行一个券码"
          >
            <Input.TextArea
              rows={10}
              maxLength={505_000}
              placeholder={"COUPON001\nCOUPON002\nCOUPON003"}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={selectedPool ? `券码池：${selectedPool.name}` : "券码明细"}
        open={codeModalOpen}
        onCancel={() => {
          setCodeModalOpen(false);
          setSelectedPool(null);
        }}
        footer={null}
        width={700}
      >
        <Table
          dataSource={poolCodes}
          columns={poolCodeColumns}
          rowKey="id"
          loading={poolCodesLoading}
          size="small"
          pagination={{
            current: poolCodesPage,
            total: poolCodesTotal,
            pageSize: 50,
            onChange: setPoolCodesPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Modal>
    </>
  );
}
