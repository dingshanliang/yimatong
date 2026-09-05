"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Select,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ExclamationCircleOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api, { extractErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import {
  channelAccessForPrincipal,
  type ChannelAccess,
} from "@/lib/channel-access";
import { formatDateTime } from "@/lib/format";
import { STATUS_COLORS } from "@/lib/status-colors";

const { Title } = Typography;

interface DiversionClue {
  id: string;
  public_id: string;
  code_item_id: string;
  expected_region: string | null;
  detected_city: string | null;
  distributor_name?: string | null;
  region_name?: string | null;
  severity?: string | null;
  resolved: boolean;
  resolution_action?: string | null;
  version: number;
  created_at?: string;
}

const SEVERITY_MAP: Record<string, { label: string; color: string }> = {
  high: { label: "高危", color: STATUS_COLORS.error },
  medium: { label: "中危", color: STATUS_COLORS.warning },
};

const RESOLUTION_OPTIONS = [
  { value: "confirmed_diversion", label: "确认窜货" },
  { value: "false_positive", label: "误报" },
  { value: "normal_transfer", label: "正常调货" },
];

export default function AntiDiversionPage() {
  const user = useAuthStore((state) => state.user);
  const access = channelAccessForPrincipal(user);
  if (!access.canRead) {
    return <Alert type="warning" message="当前账号无渠道调查权限" />;
  }
  return <AntiDiversionWorkspace access={access} />;
}

function AntiDiversionWorkspace({ access }: { access: ChannelAccess }) {
  const { message } = App.useApp();

  const [clues, setClues] = useState<DiversionClue[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [resolvedFilter, setResolvedFilter] = useState<boolean | undefined>(
    undefined
  );
  const [stats, setStats] = useState({ total: 0, unresolved: 0 });
  const [resolveModalOpen, setResolveModalOpen] = useState(false);
  const [resolveAction, setResolveAction] = useState("confirmed_diversion");
  const [resolutionNote, setResolutionNote] = useState("");
  const [resolvingClue, setResolvingClue] = useState<DiversionClue | null>(
    null
  );
  const [submitting, setSubmitting] = useState(false);

  const loadClues = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number | boolean> = {
        page,
        page_size: 20,
      };
      if (resolvedFilter !== undefined) params.resolved = resolvedFilter;
      const { data } = await api.get("/channels/diversion-clues", { params });
      setClues(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载窜货线索失败");
    } finally {
      setLoading(false);
    }
  }, [page, resolvedFilter, message]);

  const loadStats = useCallback(async () => {
    try {
      // 加载统计数据
      const [allRes, unresolvedRes] = await Promise.all([
        api.get("/channels/diversion-clues", { params: { page_size: 1 } }),
        api.get("/channels/diversion-clues", {
          params: { page_size: 1, resolved: false },
        }),
      ]);
      setStats({
        total: allRes.data?.total || 0,
        unresolved: unresolvedRes.data?.total || 0,
      });
    } catch {
      // 静默忽略统计错误
    }
  }, []);

  useEffect(() => {
    loadClues();
  }, [loadClues]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const handleResolve = async (
    clue: DiversionClue,
    action: string
  ): Promise<boolean> => {
    // 后端 DiversionTransitionRequest.reason 上限 500 字符
    if (resolutionNote.trim().length > 500) {
      message.error("结论依据不能超过 500 字");
      return false;
    }
    setSubmitting(true);
    try {
      await api.post(
        `/risk-dashboard/diversion-clues/${clue.id}/transition`,
        {
          expected_version: clue.version,
          to_status: action,
          reason: resolutionNote,
          resolution_note: resolutionNote,
        },
        { headers: { "Idempotency-Key": crypto.randomUUID() } }
      );
      message.success("线索已处理");
      loadClues();
      loadStats();
      return true;
    } catch (e: unknown) {
      message.error(extractErrorMessage(e, "处理失败，请重试"));
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const showResolveModal = (clue: DiversionClue) => {
    setResolvingClue(clue);
    setResolveAction("confirmed_diversion");
    setResolutionNote("");
    setResolveModalOpen(true);
  };

  const columns: ColumnsType<DiversionClue> = [
    {
      title: "码编号",
      dataIndex: "public_id",
      key: "public_id",
      width: 140,
      render: (v: string) => <Typography.Text copyable>{v}</Typography.Text>,
    },
    {
      title: "严重程度",
      dataIndex: "severity",
      key: "severity",
      width: 80,
      render: (v: string) => {
        const info = SEVERITY_MAP[v] || {
          label: v || "未知",
          color: STATUS_COLORS.neutral,
        };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "预期区域",
      dataIndex: "expected_region",
      key: "expected_region",
      ellipsis: true,
    },
    {
      title: "实际扫码地",
      dataIndex: "detected_city",
      key: "detected_city",
      width: 120,
    },
    {
      title: "经销商",
      dataIndex: "distributor_name",
      key: "distributor_name",
      width: 120,
      render: (v: string | null) => v || "-",
    },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      width: 80,
      render: (v: boolean) =>
        v ? (
          <Tag icon={<CheckCircleOutlined />} color={STATUS_COLORS.success}>
            已处理
          </Tag>
        ) : (
          <Tag
            icon={<ExclamationCircleOutlined />}
            color={STATUS_COLORS.warning}
          >
            待处理
          </Tag>
        ),
    },
    {
      title: "处理结果",
      dataIndex: "resolution_action",
      key: "resolution_action",
      width: 100,
      render: (v: string | null) => {
        if (!v) return "-";
        const opt = RESOLUTION_OPTIONS.find((o) => o.value === v);
        return opt?.label || v;
      },
    },
    {
      title: "发现时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (v?: string) => formatDateTime(v),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, record: DiversionClue) =>
        record.resolved || !access.canManage ? null : (
          <Button
            size="small"
            type="primary"
            onClick={() => showResolveModal(record)}
          >
            处理
          </Button>
        ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          疑似窜货看板
        </Title>
        <Select
          placeholder="按状态筛选"
          allowClear
          style={{ width: 150 }}
          value={resolvedFilter}
          onChange={(v) => {
            setResolvedFilter(v);
            setPage(1);
          }}
          options={[
            { value: false, label: "待处理" },
            { value: true, label: "已处理" },
          ]}
        />
      </div>

      <Row gutter={[16, 16]} className="mb-4">
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic title="全部线索" value={stats.total} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic
              title="待处理"
              value={stats.unresolved}
              styles={{
                value: {
                  color:
                    stats.unresolved > 0
                      ? "var(--ymt-color-feedback-danger)"
                      : undefined,
                },
              }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic
              title="处理率"
              value={
                stats.total > 0
                  ? Math.round(
                      ((stats.total - stats.unresolved) / stats.total) * 100
                    )
                  : 0
              }
              suffix="%"
            />
          </Card>
        </Col>
      </Row>

      <Modal
        title="处理窜货线索"
        open={resolveModalOpen}
        confirmLoading={submitting}
        onOk={async () => {
          // 提交成功后才关闭弹窗，失败时保留现场供修改重试
          if (!resolvingClue) {
            setResolveModalOpen(false);
            return;
          }
          const ok = await handleResolve(resolvingClue, resolveAction);
          if (ok) {
            setResolveModalOpen(false);
            setResolvingClue(null);
            setResolutionNote("");
          }
        }}
        onCancel={() => {
          setResolveModalOpen(false);
          setResolvingClue(null);
        }}
        okButtonProps={{ disabled: !resolutionNote.trim() }}
      >
        {resolvingClue && (
          <div className="mt-3">
            <p>
              <strong>码编号：</strong>
              {resolvingClue.public_id}
            </p>
            <p>
              <strong>预期区域：</strong>
              {resolvingClue.expected_region || "未分配"}
            </p>
            <p>
              <strong>实际扫码地：</strong>
              {resolvingClue.detected_city || "未知"}
            </p>
            <div className="mt-3">
              <strong>处理结果：</strong>
              <Select
                style={{ width: "100%", marginTop: 8 }}
                value={resolveAction}
                onChange={(v) => setResolveAction(v)}
                options={RESOLUTION_OPTIONS}
              />
              <Input.TextArea
                className="mt-3"
                aria-label="结论依据"
                placeholder="填写结论依据"
                maxLength={500}
                showCount
                value={resolutionNote}
                onChange={(event) => setResolutionNote(event.target.value)}
              />
            </div>
          </div>
        )}
      </Modal>

      <Card size="small">
        <Table
          columns={columns}
          dataSource={clues}
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
      </Card>
    </div>
  );
}
