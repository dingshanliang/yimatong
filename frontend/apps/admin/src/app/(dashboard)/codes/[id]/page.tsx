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
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, DownloadOutlined } from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";

const { Title, Text } = Typography;

interface CodeBatchDetail {
  id: string;
  batch_code: string;
  quantity: number;
  product_id: string;
  sku_id: string;
  production_batch_id?: string;
  code_type: string;
  generation_mode: string;
  status: string;
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

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "待生成", color: "#8c8c8c" },
  generating: { label: "生成中", color: "#1d4ed8" },
  completed: { label: "已生成", color: "#16a34a" },
  exported: { label: "已导出", color: "#1d4ed8" },
  printing: { label: "印刷中", color: "#f59e0b" },
  delivered: { label: "已交付", color: "#f59e0b" },
  activated: { label: "已激活", color: "#1d4ed8" },
  failed: { label: "失败", color: "#b91c1c" },
  created: { label: "已创建", color: "#8c8c8c" },
  bound: { label: "已绑定", color: "#16a34a" },
  expired: { label: "已过期", color: "#8c8c8c" },
  revoked: { label: "已撤销", color: "#b91c1c" },
  frozen: { label: "已冻结", color: "#f59e0b" },
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
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message, modal } = App.useApp();

  const [batch, setBatch] = useState<CodeBatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activating, setActivating] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [markingPrinting, setMarkingPrinting] = useState(false);
  const [markingDelivered, setMarkingDelivered] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/code-batches/${params.id}`);
      setBatch(data);
    } catch (err) {
      setBatch(null);
      message.error(extractErrorMessage(err, "加载码批次详情失败"));
    } finally {
      setLoading(false);
    }
  }, [params.id, message]);

  useEffect(() => {
    load();
  }, [load]);

  const handleActivate = async () => {
    if (!batch) return;
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
    if (!batch) return;
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
      link.download = `codes-${batch.id}.csv`;
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
    if (!batch) return;
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

  const handleMarkDelivered = async () => {
    if (!batch) return;
    setMarkingDelivered(true);
    try {
      await api.post(`/code-batches/${batch.id}/mark-delivered`);
      message.success("已标记为已交付");
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
    color: "#8c8c8c",
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
                    const color = STATUS_MAP[key]?.color || "default";
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
            <Space direction="vertical" style={{ width: "100%" }}>
              {[
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
                >
                  导出码表
                </Button>
              )}
              {batch.status === "completed" && (
                <>
                  <Button
                    block
                    type="primary"
                    onClick={showActivateConfirm}
                    loading={activating}
                  >
                    激活码批次
                  </Button>
                  <Button
                    block
                    onClick={handleMarkPrinting}
                    loading={markingPrinting}
                  >
                    标记印刷中
                  </Button>
                </>
              )}
              {batch.status === "printing" && (
                <Button
                  block
                  onClick={handleMarkDelivered}
                  loading={markingDelivered}
                >
                  标记已交付
                </Button>
              )}
              {batch.status === "delivered" && (
                <Button
                  block
                  type="primary"
                  onClick={showActivateConfirm}
                  loading={activating}
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
    </div>
  );
}
