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
  Table,
  Tag,
  Typography,
} from "antd";
import { ArrowLeftOutlined, DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import {
  newExportIdempotencyKey,
  useExportReasonDialog,
} from "@/components/ExportReasonDialog";
import { useAuthStore } from "@/lib/auth";
import { codeAccessForPrincipal, type CodeAccess } from "@/lib/code-access";
import { formatDate, formatDateTime } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";
import {
  useTenantFeatureEnabled,
  useTenantPlanReadOnly,
} from "../../_components/TenantPlanReadOnly";

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

interface CodeItemRow {
  id: string;
  public_id: string;
  status: string;
  code_type: string;
}

interface ItemVoidFormValues {
  reason: string;
  confirm: "void";
}

interface ItemFreezeFormValues {
  reason: string;
  confirm: "freeze";
}

type BatchFreezeFormValues = ItemFreezeFormValues;
type BatchVoidFormValues = ItemVoidFormValues;

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

interface BatchTerminalDisplay {
  label: string;
  color: string;
}

/**
 * 整批冻结/作废后 batch.status 仍停留在 activated，这里用已加载的码明细
 * 派生批次终态展示：全部作废 → 「已作废」；全部处于冻结/作废且至少一个
 * 冻结 → 「已冻结」。无法判定（明细未加载或状态混合）时返回 null，
 * 沿用 batch.status 原始展示。
 */
function deriveTerminalBatchDisplay(
  items: Pick<CodeItemRow, "status">[]
): BatchTerminalDisplay | null {
  if (items.length === 0) return null;
  const statuses = new Set(items.map((item) => item.status));
  if (statuses.size === 1 && statuses.has("revoked")) {
    return { label: "已作废", color: STATUS_COLORS.error };
  }
  const allFrozenOrRevoked =
    statuses.has("frozen") &&
    [...statuses].every(
      (status) => status === "frozen" || status === "revoked"
    );
  if (allFrozenOrRevoked) {
    return { label: "已冻结", color: STATUS_COLORS.warning };
  }
  return null;
}

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
  const { requestReason, exportReasonDialog } = useExportReasonDialog();
  const planReadOnly = useTenantPlanReadOnly();
  const riskLifecycleEnabled = useTenantFeatureEnabled("risk_module");
  const [deliveryForm] = Form.useForm<DeliveryFormValues>();
  const [batchFreezeForm] = Form.useForm<BatchFreezeFormValues>();
  const [batchVoidForm] = Form.useForm<BatchVoidFormValues>();

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
  const [batchFreezeOpen, setBatchFreezeOpen] = useState(false);
  const [batchVoidOpen, setBatchVoidOpen] = useState(false);
  const [batchLifecycleLoading, setBatchLifecycleLoading] = useState(false);
  const [itemRefreshKey, setItemRefreshKey] = useState(0);
  const [panelItems, setPanelItems] = useState<CodeItemRow[]>([]);
  const handlePanelItemsLoaded = useCallback((items: CodeItemRow[]) => {
    setPanelItems(items);
  }, []);

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
    } catch (error) {
      message.error(extractErrorMessage(error, "激活失败"));
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
    const reason = await requestReason();
    if (!reason) return;
    setExporting(true);
    try {
      const response = await api.post<Blob>(
        `/code-batches/${batch.id}/export`,
        { reason },
        {
          headers: { "Idempotency-Key": newExportIdempotencyKey() },
          responseType: "blob",
        }
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
    } catch (error) {
      message.error(
        extractErrorMessage(error, "导出失败，请确认码批次状态和账号权限")
      );
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
    } catch (error) {
      message.error(extractErrorMessage(error, "标记印刷中失败"));
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
    } catch (error) {
      message.error(extractErrorMessage(error, "标记已交付失败"));
    } finally {
      setMarkingDelivered(false);
    }
  };

  const handleBatchFreeze = async (values: BatchFreezeFormValues) => {
    if (!batch || planReadOnly || !access.canManage) return;
    setBatchLifecycleLoading(true);
    try {
      await api.post(`/code-batches/${batch.id}/freeze`, {
        reason: values.reason.trim(),
        confirm: "freeze",
      });
      message.success("整批码已冻结");
      setBatchFreezeOpen(false);
      batchFreezeForm.resetFields();
      setItemRefreshKey((key) => key + 1);
      await load();
    } catch (error) {
      message.error(
        extractErrorMessage(error, "整批冻结失败，请刷新状态后重试")
      );
    } finally {
      setBatchLifecycleLoading(false);
    }
  };

  const handleBatchVoid = async (values: BatchVoidFormValues) => {
    if (!batch || planReadOnly || !access.canManage) return;
    setBatchLifecycleLoading(true);
    try {
      await api.post(`/code-batches/${batch.id}/void`, null, {
        params: { reason: values.reason.trim(), confirm: "void" },
      });
      message.success("整批码已永久作废");
      setBatchVoidOpen(false);
      batchVoidForm.resetFields();
      setItemRefreshKey((key) => key + 1);
      await load();
    } catch (error) {
      message.error(
        extractErrorMessage(error, "整批作废失败，请刷新状态后重试")
      );
    } finally {
      setBatchLifecycleLoading(false);
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

  const terminalDisplay = deriveTerminalBatchDisplay(panelItems);
  const statusInfo = terminalDisplay ||
    STATUS_MAP[batch.status] || {
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
      {exportReasonDialog}
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
                !terminalDisplay &&
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
              {access.canManage &&
                batch.status === "activated" &&
                !terminalDisplay && (
                  <>
                    <Button
                      block
                      onClick={() => setBatchFreezeOpen(true)}
                      disabled={planReadOnly}
                    >
                      冻结整批
                    </Button>
                    <Button
                      block
                      danger
                      onClick={() => setBatchVoidOpen(true)}
                      disabled={planReadOnly}
                    >
                      永久作废整批
                    </Button>
                  </>
                )}
              {(![
                "activated",
                "completed",
                "exported",
                "printing",
                "delivered",
              ].includes(batch.status) ||
                (terminalDisplay !== null &&
                  !["exported", "printing", "delivered"].includes(
                    batch.status
                  ))) && <Text type="secondary">当前状态暂无可用操作</Text>}
            </Space>
          </Card>
        </Col>
      </Row>
      {access.canManage ? (
        <CodeItemsLifecyclePanel
          batchId={batch.id}
          disabled={planReadOnly}
          riskLifecycleEnabled={riskLifecycleEnabled}
          refreshKey={itemRefreshKey}
          batchStatus={batch.status}
          onItemsLoaded={handlePanelItemsLoaded}
        />
      ) : null}
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
      <Modal
        title="冻结整批码"
        open={batchFreezeOpen}
        onCancel={() => {
          setBatchFreezeOpen(false);
          batchFreezeForm.resetFields();
        }}
        onOk={() => batchFreezeForm.submit()}
        okText="确认冻结整批"
        confirmLoading={batchLifecycleLoading}
        okButtonProps={{ disabled: planReadOnly || !access.canManage }}
        forceRender
      >
        <Form<BatchFreezeFormValues>
          name="code-batch-freeze"
          form={batchFreezeForm}
          layout="vertical"
          initialValues={{ confirm: "freeze" }}
          disabled={planReadOnly || !access.canManage}
          onFinish={handleBatchFreeze}
        >
          <Form.Item
            name="reason"
            label="整批冻结原因"
            rules={[{ required: true, whitespace: true, max: 200 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="confirm" hidden>
            <input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="永久作废整批码"
        open={batchVoidOpen}
        onCancel={() => {
          setBatchVoidOpen(false);
          batchVoidForm.resetFields();
        }}
        onOk={() => batchVoidForm.submit()}
        okText="确认作废整批"
        confirmLoading={batchLifecycleLoading}
        okButtonProps={{
          danger: true,
          disabled: planReadOnly || !access.canManage,
        }}
        forceRender
      >
        <Alert
          className="mb-3"
          type="warning"
          showIcon
          title="整批作废后不可恢复，所有码都会停止使用。"
        />
        <Form<BatchVoidFormValues>
          name="code-batch-void"
          form={batchVoidForm}
          layout="vertical"
          initialValues={{ confirm: "void" }}
          disabled={planReadOnly || !access.canManage}
          onFinish={handleBatchVoid}
        >
          <Form.Item
            name="reason"
            label="整批作废原因"
            rules={[{ required: true, whitespace: true, max: 200 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="confirm" hidden>
            <input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function CodeItemsLifecyclePanel({
  batchId,
  disabled,
  riskLifecycleEnabled,
  refreshKey,
  batchStatus,
  onItemsLoaded,
}: {
  batchId: string;
  disabled: boolean;
  riskLifecycleEnabled: boolean;
  refreshKey: number;
  batchStatus: string;
  onItemsLoaded: (items: CodeItemRow[]) => void;
}) {
  const { message, modal } = App.useApp();
  const [voidForm] = Form.useForm<ItemVoidFormValues>();
  const [freezeForm] = Form.useForm<ItemFreezeFormValues>();
  const [items, setItems] = useState<CodeItemRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [mutatingId, setMutatingId] = useState<string | null>(null);
  const [voidTarget, setVoidTarget] = useState<CodeItemRow | null>(null);
  const [freezeTarget, setFreezeTarget] = useState<CodeItemRow | null>(null);

  const loadItems = useCallback(
    async (targetPage: number) => {
      setLoading(true);
      setError(false);
      try {
        const { data } = await api.get("/code-items", {
          params: { code_batch_id: batchId, page: targetPage, page_size: 20 },
        });
        const nextItems: CodeItemRow[] = data.items || [];
        setItems(nextItems);
        setTotal(data.total || 0);
        setPage(targetPage);
        onItemsLoaded(nextItems);
      } catch {
        setItems([]);
        setTotal(0);
        setError(true);
        onItemsLoaded([]);
      } finally {
        setLoading(false);
      }
    },
    [batchId, onItemsLoaded]
  );

  useEffect(() => {
    void loadItems(1);
  }, [loadItems, refreshKey]);

  const freezeItem = async (values: ItemFreezeFormValues) => {
    if (!freezeTarget || disabled || !riskLifecycleEnabled) return;
    setMutatingId(freezeTarget.id);
    try {
      await api.post(`/risk-alerts/code-items/${freezeTarget.id}/freeze`, {
        reason: values.reason.trim(),
        confirm: "freeze",
      });
      message.success("码已冻结，消费者权益入口同步关闭");
      setFreezeTarget(null);
      freezeForm.resetFields();
      await loadItems(page);
    } catch (error) {
      message.error(extractErrorMessage(error, "冻结失败，请刷新状态后重试"));
    } finally {
      setMutatingId(null);
    }
  };

  const recoverItem = async (item: CodeItemRow) => {
    if (disabled || !riskLifecycleEnabled) return;
    setMutatingId(item.id);
    try {
      await api.post(`/risk-alerts/code-items/${item.id}/unfreeze`);
      message.success("码已恢复到冻结前状态");
      await loadItems(page);
    } catch (error) {
      message.error(extractErrorMessage(error, "恢复失败，请刷新状态后重试"));
    } finally {
      setMutatingId(null);
    }
  };

  const voidItem = async (values: ItemVoidFormValues) => {
    if (!voidTarget || disabled) return;
    setMutatingId(voidTarget.id);
    try {
      await api.post(`/code-items/${voidTarget.id}/revoke`, {
        reason: values.reason.trim(),
        confirm: "void",
      });
      message.success("码已永久作废");
      setVoidTarget(null);
      voidForm.resetFields();
      await loadItems(page);
    } catch (error) {
      message.error(extractErrorMessage(error, "作废失败，请刷新状态后重试"));
    } finally {
      setMutatingId(null);
    }
  };

  const columns: ColumnsType<CodeItemRow> = [
    { title: "码编号", dataIndex: "public_id", key: "public_id" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = STATUS_MAP[status] || {
          label: status,
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_value, item) => {
        let transitionAction: React.ReactNode = null;
        if (
          riskLifecycleEnabled &&
          (item.status === "activated" || item.status === "bound")
        ) {
          transitionAction = (
            <Button
              size="small"
              disabled={disabled}
              loading={mutatingId === item.id}
              onClick={() => {
                freezeForm.resetFields();
                setFreezeTarget(item);
              }}
            >
              冻结
            </Button>
          );
        }
        if (riskLifecycleEnabled && item.status === "frozen") {
          transitionAction = (
            <Button
              size="small"
              disabled={disabled}
              loading={mutatingId === item.id}
              onClick={() =>
                modal.confirm({
                  title: "恢复该码？",
                  content: "系统会恢复到冻结前的已激活或已绑定状态。",
                  okText: "确认恢复",
                  cancelText: "取消",
                  onOk: () => recoverItem(item),
                })
              }
            >
              恢复
            </Button>
          );
        }
        const canVoid = !["revoked", "expired"].includes(item.status);
        if (!transitionAction && !canVoid) {
          return <Text type="secondary">暂无可用操作</Text>;
        }
        return (
          <Space>
            {transitionAction}
            {canVoid ? (
              <Button
                size="small"
                danger
                disabled={disabled}
                onClick={() => {
                  voidForm.resetFields();
                  setVoidTarget(item);
                }}
              >
                永久作废
              </Button>
            ) : null}
          </Space>
        );
      },
    },
  ];

  if (error) {
    return (
      <Alert
        className="mt-4"
        type="error"
        showIcon
        title="码明细加载失败"
        action={
          <Button size="small" onClick={() => void loadItems(page)}>
            重试
          </Button>
        }
      />
    );
  }

  return (
    <>
      <Card title="码明细与生命周期" size="small" className="mt-4">
        <Table<CodeItemRow>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={items}
          loading={loading}
          pagination={{
            current: page,
            total,
            pageSize: 20,
            onChange: (next) => void loadItems(next),
            showTotal: (count) => `共 ${count} 个码`,
          }}
        />
      </Card>
      {riskLifecycleEnabled ? (
        <Modal
          title="冻结该码"
          open={Boolean(freezeTarget)}
          onCancel={() => {
            setFreezeTarget(null);
            freezeForm.resetFields();
          }}
          onOk={() => freezeForm.submit()}
          okText="确认冻结"
          okButtonProps={{ disabled }}
          confirmLoading={mutatingId === freezeTarget?.id}
          forceRender
        >
          <Alert
            className="mb-3"
            type="warning"
            showIcon
            title="冻结后仍可查看溯源，但消费者权益入口会立即暂停。"
          />
          <Form<ItemFreezeFormValues>
            name="code-item-freeze"
            form={freezeForm}
            layout="vertical"
            initialValues={{ confirm: "freeze" }}
            disabled={disabled}
            onFinish={freezeItem}
          >
            <Form.Item
              name="reason"
              label="冻结原因"
              rules={[{ required: true, whitespace: true, max: 200 }]}
            >
              <Input.TextArea rows={3} />
            </Form.Item>
            <Form.Item name="confirm" hidden>
              <input />
            </Form.Item>
          </Form>
        </Modal>
      ) : null}
      <Modal
        title="永久作废该码"
        open={Boolean(voidTarget)}
        onCancel={() => {
          setVoidTarget(null);
          voidForm.resetFields();
        }}
        onOk={() => voidForm.submit()}
        okText="确认作废"
        okButtonProps={{ danger: true, disabled }}
        confirmLoading={mutatingId === voidTarget?.id}
        forceRender
      >
        <Alert
          className="mb-3"
          type="warning"
          showIcon
          title="作废后不可恢复，消费者将无法再使用该码。"
        />
        {batchStatus === "completed" ? (
          <Alert
            className="mb-3"
            type="warning"
            showIcon
            title="作废后该批次将无法导出码表，请确认是否继续。"
          />
        ) : null}
        <Form<ItemVoidFormValues>
          name="code-item-void"
          form={voidForm}
          layout="vertical"
          initialValues={{ confirm: "void" }}
          disabled={disabled}
          onFinish={voidItem}
        >
          <Form.Item
            name="reason"
            label="作废原因"
            rules={[{ required: true, whitespace: true, max: 200 }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="confirm" hidden>
            <input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
