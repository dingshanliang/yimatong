"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, DownloadOutlined } from "@ant-design/icons";
import api from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { codeAccessForPrincipal, type CodeAccess } from "@/lib/code-access";
import { formatDate, formatDateTime } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useTenantPlanReadOnly } from "../../_components/TenantPlanReadOnly";

const { Title, Text } = Typography;

interface CodeBatchDetail {
  id: string;
  batch_code: string;
  quantity: number;
  expected_item_count?: number;
  product_id: string;
  sku_id: string;
  production_batch_id?: string;
  code_type: string;
  generation_mode: string;
  status: string;
  source?: "generated" | "imported";
  product_name?: string;
  sku_name?: string;
  sku_code?: string;
  production_batch_code?: string;
  production_date?: string;
  production_origin?: string;
  created_by: string;
  created_at?: string;
  stats?: Record<string, number>;
}

interface DeliveryFormValues {
  reason: string;
  recipient: string;
  confirm: "deliver";
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: STATUS_COLORS.neutral },
  generating: { label: "生成中", color: STATUS_COLORS.processing },
  completed: { label: "已生成", color: STATUS_COLORS.success },
  exported: { label: "已导出", color: STATUS_COLORS.processing },
  printing: { label: "印刷中", color: STATUS_COLORS.warning },
  delivered: { label: "已交付", color: STATUS_COLORS.warning },
  activated: { label: "已激活", color: STATUS_COLORS.processing },
  failed: { label: "失败", color: STATUS_COLORS.error },
  created: { label: "已创建", color: STATUS_COLORS.neutral },
  bound: { label: "已绑定", color: STATUS_COLORS.success },
  expired: { label: "已过期", color: STATUS_COLORS.neutral },
  revoked: { label: "已撤销", color: STATUS_COLORS.error },
  frozen: { label: "已冻结", color: STATUS_COLORS.warning },
};

const CODE_TYPE_OPTIONS: Record<string, string> = {
  single: "普通二维码",
  paired: "内外双码",
  outer: "外包装码（引流）",
  inner: "内包装码（验真）",
};

const GENERATION_MODE_LABELS: Record<string, string> = {
  item_level: "一物一码",
  batch_level: "一批一码",
};

export default function CodeBatchDetailPage() {
  const user = useAuthStore((state) => state.user);
  const access = codeAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问码管理" />;
  }

  return <CodeBatchDetailWorkspace access={access} />;
}

function CodeBatchDetailWorkspace({ access }: { access: CodeAccess }) {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message, modal } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const [deliveryForm] = Form.useForm<DeliveryFormValues>();

  const [batch, setBatch] = useState<CodeBatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadState, setLoadState] = useState<"not_found" | "error" | null>(
    null
  );
  const [activating, setActivating] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [markingPrinting, setMarkingPrinting] = useState(false);
  const [markingDelivered, setMarkingDelivered] = useState(false);
  const [deliveryOpen, setDeliveryOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadState(null);
    try {
      const { data } = await api.get(`/code-batches/${params.id}`);
      setBatch(data);
    } catch (err) {
      setBatch(null);
      const status = (err as { response?: { status?: number } })?.response
        ?.status;
      setLoadState(status === 404 ? "not_found" : "error");
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const handleActivate = async () => {
    if (!batch || planReadOnly || !access.canManage) return;
    setActivating(true);
    try {
      await api.post(`/code-batches/${batch.id}/activate`);
      message.success("码批次已激活");
      load();
    } catch {
      message.error("激活失败");
    } finally {
      setActivating(false);
    }
  };

  const showActivateConfirm = () => {
    if (!batch) return;
    modal.confirm({
      title: "激活码批次",
      content: (
        <div className="mt-3">
          <Alert
            className="mb-3"
            type="warning"
            showIcon
            title="激活后，该批二维码将对消费者扫码生效。请确认码表已导出，并已完成印刷或贴码安排。"
          />
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="产品">
              {batch.product_name || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="SKU">
              {batch.sku_name || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="生产批次">
              {batch.production_batch_code || "未关联"}
            </Descriptions.Item>
            <Descriptions.Item label="数量">
              {Number(batch.quantity).toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="码类型">
              {CODE_TYPE_OPTIONS[batch.code_type] || batch.code_type}
            </Descriptions.Item>
          </Descriptions>
        </div>
      ),
      okText: "确认激活",
      cancelText: "取消",
      onOk: handleActivate,
    });
  };

  const handleExport = async () => {
    if (!batch || planReadOnly || !access.canExport) return;
    setExporting(true);
    try {
      const response = await api.post<Blob>(
        `/code-batches/${batch.id}/export`,
        null,
        { responseType: "blob" }
      );
      const blob = response.data;
      if (!blob || blob.size === 0) {
        message.warning("当前码批次暂无可导出的码，请检查生成状态");
        return;
      }
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const contentDisposition = response.headers["content-disposition"] as
        string | undefined;
      const filename = contentDisposition?.match(/filename="?([^";]+)"?/i)?.[1];
      link.download = filename || `codes-${batch.id}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      message.success("码表已导出");
    } catch {
      message.error("导出失败，请确认码批次状态和账号权限");
    } finally {
      setExporting(false);
    }
  };

  const handleMarkPrinting = async () => {
    if (!batch || planReadOnly || !access.canManage) return;
    setMarkingPrinting(true);
    try {
      await api.post(`/code-batches/${batch.id}/mark-printing`);
      message.success("已标记为印刷中");
      load();
    } catch {
      message.error("标记印刷中失败");
    } finally {
      setMarkingPrinting(false);
    }
  };

  const handleMarkDelivered = async (values: DeliveryFormValues) => {
    if (!batch || planReadOnly || !access.canManage) return;
    setMarkingDelivered(true);
    try {
      await api.post(`/code-batches/${batch.id}/mark-delivered`, values);
      message.success("已标记为已交付");
      setDeliveryOpen(false);
      deliveryForm.resetFields();
      load();
    } catch {
      message.error("标记已交付失败");
    } finally {
      setMarkingDelivered(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Spin size="large" />
      </div>
    );
  }

  if (!batch && loadState === "error") {
    return (
      <Alert
        type="error"
        showIcon
        title="码批次详情加载失败"
        action={
          <Button size="small" onClick={() => void load()}>
            重试
          </Button>
        }
      />
    );
  }

  if (!batch) {
    return (
      <div className="py-10 text-center">
        <Title level={4} type="secondary">
          码批次不存在或已被删除
        </Title>
        <Button type="primary" onClick={() => router.push("/codes")}>
          返回码管理
        </Button>
      </div>
    );
  }

  const statusInfo = STATUS_MAP[batch.status] || {
    label: batch.status,
    color: STATUS_COLORS.neutral,
  };
  const stats = batch.stats || {};
  const totalStats = Object.values(stats).reduce((a, b) => a + b, 0);
  const activatedCount = stats.activated || stats.scanned || 0;
  const activatedPercent =
    totalStats > 0 ? Math.round((activatedCount / batch.quantity) * 100) : 0;

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => router.push("/codes")}
        >
          返回
        </Button>
        <Title level={4} className="!mb-0">
          {batch.batch_code}
        </Title>
        <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card title="基本信息" size="small">
            <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
              <Descriptions.Item label="批次号">
                {batch.batch_code}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="产品">
                {batch.product_name || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="SKU">
                {batch.sku_name || "-"}
                {batch.sku_code ? `（${batch.sku_code}）` : ""}
              </Descriptions.Item>
              <Descriptions.Item label="生产批次">
                {batch.production_batch_code || "未关联"}
              </Descriptions.Item>
              <Descriptions.Item label="生产日期">
                {formatDate(batch.production_date)}
              </Descriptions.Item>
              <Descriptions.Item label="产地">
                {batch.production_origin || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="生成方式">
                {GENERATION_MODE_LABELS[batch.generation_mode] ||
                  batch.generation_mode}
              </Descriptions.Item>
              <Descriptions.Item label="数量">
                {Number(batch.quantity).toLocaleString()}
                {batch.code_type === "paired" ? " 组" : ""}
              </Descriptions.Item>
              <Descriptions.Item label="物理码数量">
                {batch.expected_item_count == null
                  ? "-"
                  : Number(batch.expected_item_count).toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="码来源">
                {batch.source === "imported" ? "接管已有印刷码" : "系统生成"}
              </Descriptions.Item>
              <Descriptions.Item label="码类型">
                {CODE_TYPE_OPTIONS[batch.code_type] || batch.code_type}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {formatDateTime(batch.created_at)}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card title="码生成统计" size="small" style={{ marginBottom: 16 }}>
            {totalStats > 0 ? (
              <>
                <div className="mb-3">
                  <Text type="secondary">激活进度</Text>
                  <Progress percent={activatedPercent} size="small" />
                </div>
                <Row gutter={[8, 8]}>
                  {Object.entries(stats).map(([key, value]) => {
                    const label = STATUS_MAP[key]?.label || key;
                    const color =
                      STATUS_MAP[key]?.color || STATUS_COLORS.neutral;
                    return (
                      <Col span={12} key={key}>
                        <Statistic
                          title={<Tag color={color}>{label}</Tag>}
                          value={value}
                        />
                      </Col>
                    );
                  })}
                </Row>
              </>
            ) : (
              <Text type="secondary">暂无统计数据</Text>
            )}
          </Card>

          <Card title="操作" size="small">
            <Space orientation="vertical" style={{ width: "100%" }}>
              {access.canExport &&
                [
                  "activated",
                  "completed",
                  "exported",
                  "printing",
                  "delivered",
                ].includes(batch.status) && (
                  <Button
                    block
                    icon={<DownloadOutlined />}
                    onClick={handleExport}
                    loading={exporting}
                    disabled={planReadOnly}
                  >
                    导出码表
                  </Button>
                )}
              {access.canManage && batch.status === "exported" && (
                <>
                  <Button
                    block
                    onClick={handleMarkPrinting}
                    loading={markingPrinting}
                    disabled={planReadOnly}
                  >
                    标记印刷中
                  </Button>
                </>
              )}
              {access.canManage && batch.status === "printing" && (
                <Button
                  block
                  onClick={() => setDeliveryOpen(true)}
                  loading={markingDelivered}
                  disabled={planReadOnly}
                >
                  标记已交付
                </Button>
              )}
              {access.canManage && batch.status === "delivered" && (
                <Button
                  block
                  type="primary"
                  onClick={showActivateConfirm}
                  loading={activating}
                  disabled={planReadOnly}
                >
                  激活码批次
                </Button>
              )}
              {![
                "activated",
                "completed",
                "exported",
                "printing",
                "delivered",
              ].includes(batch.status) && (
                <Text type="secondary">当前状态暂无可用操作</Text>
              )}
            </Space>
          </Card>
        </Col>
      </Row>
      <Modal
        title="确认码表已交付"
        open={deliveryOpen}
        onCancel={() => {
          setDeliveryOpen(false);
          deliveryForm.resetFields();
        }}
        onOk={() => deliveryForm.submit()}
        okText="确认交付"
        confirmLoading={markingDelivered}
        okButtonProps={{ disabled: planReadOnly || !access.canManage }}
        forceRender
      >
        <Form<DeliveryFormValues>
          form={deliveryForm}
          layout="vertical"
          onFinish={handleMarkDelivered}
          disabled={planReadOnly || !access.canManage}
          initialValues={{ confirm: "deliver" }}
        >
          <Form.Item
            name="recipient"
            label="交付对象"
            rules={[{ required: true, whitespace: true, max: 255 }]}
          >
            <Input placeholder="例如：华东印刷供应商" />
          </Form.Item>
          <Form.Item
            name="reason"
            label="交付说明"
            rules={[{ required: true, whitespace: true, max: 500 }]}
          >
            <Input.TextArea rows={3} placeholder="说明本次交付用途或交付单据" />
          </Form.Item>
          <Form.Item name="confirm" hidden>
            <input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
