"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  GlobalOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { RcFile } from "antd/es/upload";
import api, { extractErrorMessage } from "@/lib/api";
import { takeoverAccessForPrincipal } from "@/lib/agency-access";
import { useAuthStore } from "@/lib/auth";
import { STATUS_COLORS } from "@/lib/status-colors";
import { useTenantPlanReadOnly } from "../../_components/TenantPlanReadOnly";

const { Title, Paragraph, Text } = Typography;
const TAKEOVER_PAGE_SIZE = 20;

interface PageEnvelope<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

interface TakeoverProject {
  id: string;
  name: string;
  source_system: string;
  mode: "legacy_redirect" | "cname";
  source_domain?: string | null;
  consumer_domain?: string | null;
  domain_verification_record_name?: string | null;
  domain_verification_record_value?: string;
  status: string;
  assessment: {
    code_type: "unique" | "shared";
    recommended_mode: string;
    recommendation_reason: string;
    capabilities: Record<string, { level: string; reason: string }>;
  };
  readiness_snapshot?: ReadinessSnapshot;
}

interface ReadinessCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
  blocking?: boolean;
}

interface ReadinessSnapshot {
  checks: ReadinessCheck[];
  passed_count: number;
  total_count: number;
  ready: boolean;
}

interface ImportJob {
  id: string;
  status: string;
  file_name: string;
  counts: { total: number; valid: number; failed: number; succeeded: number };
  errors: Array<{ row_number: number; message: string; retryable: boolean }>;
}

interface RouteVersion {
  id: string;
  version: number;
  status: string;
  content_digest: string;
  sample_codes: string[];
  source_url: string;
  target_url: string;
  current_event_id?: string | null;
}

interface TakeoverEvent {
  id: string;
  action: string;
  state: string;
  created_at: string;
}

interface TakeoverObservation {
  id: string;
  transition_event_id?: string | null;
  evidence_purpose: string;
  checked_url: string;
  observed_target_url?: string | null;
  status: string;
  target_match: boolean;
  created_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  needs_fix: "待修复",
  ready_for_confirmation: "待品牌确认",
  cutover_ready: "待切换",
  pending_external: "待外部执行",
  observing: "观察中",
  completed: "已完成",
  rolling_back: "回退验证中",
  rolled_back: "已回退",
  failed: "失败",
};

const CAPABILITY_LABELS: Record<string, string> = {
  marketing: "营销",
  traceability: "溯源",
  light_verification: "轻量验真",
  diversion: "防窜观察",
  analytics: "扫码分析",
};

function statusColor(status: string) {
  if (["completed", "ready_for_confirmation"].includes(status))
    return STATUS_COLORS.success;
  if (["needs_fix", "failed", "rolled_back"].includes(status))
    return STATUS_COLORS.error;
  if (["observing", "cutover_ready", "pending_external"].includes(status))
    return STATUS_COLORS.processing;
  return STATUS_COLORS.neutral;
}

export default function TakeoverPage() {
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const access = takeoverAccessForPrincipal(user);
  const planReadOnly = useTenantPlanReadOnly();
  const [projects, setProjects] = useState<TakeoverProject[]>([]);
  const [selected, setSelected] = useState<TakeoverProject | null>(null);
  const [readiness, setReadiness] = useState<ReadinessSnapshot | null>(null);
  const [imports, setImports] = useState<ImportJob[]>([]);
  const [routes, setRoutes] = useState<RouteVersion[]>([]);
  const [events, setEvents] = useState<TakeoverEvent[]>([]);
  const [observations, setObservations] = useState<TakeoverObservation[]>([]);
  const [projectPage, setProjectPage] = useState(1);
  const [projectTotal, setProjectTotal] = useState(0);
  const [importPage, setImportPage] = useState(1);
  const [importTotal, setImportTotal] = useState(0);
  const [routePage, setRoutePage] = useState(1);
  const [routeTotal, setRouteTotal] = useState(0);
  const [auditPage, setAuditPage] = useState(1);
  const [eventTotal, setEventTotal] = useState(0);
  const [observationTotal, setObservationTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [routeOpen, setRouteOpen] = useState(false);
  const [externalRoute, setExternalRoute] = useState<RouteVersion | null>(null);
  const [probeRoute, setProbeRoute] = useState<RouteVersion | null>(null);
  const [rollbackRoute, setRollbackRoute] = useState<RouteVersion | null>(null);
  const [createForm] = Form.useForm();
  const [routeForm] = Form.useForm();
  const [externalForm] = Form.useForm();
  const [probeForm] = Form.useForm();
  const [rollbackForm] = Form.useForm();

  const selectProject = (project: TakeoverProject) => {
    setSelected(project);
    setImportPage(1);
    setRoutePage(1);
    setAuditPage(1);
  };

  const loadProjects = async () => {
    setLoading(true);
    try {
      const { data } = await api.get<PageEnvelope<TakeoverProject>>(
        "/takeovers",
        {
          params: { page: projectPage, page_size: TAKEOVER_PAGE_SIZE },
        }
      );
      setProjects(data.items || []);
      setProjectTotal(data.total || 0);
      if (selected) {
        const current = data.items.find((item) => item.id === selected.id);
        if (current) setSelected(current);
        else if (data.items?.length) selectProject(data.items[0]);
      } else if (data.items?.length) {
        selectProject(data.items[0]);
      }
    } catch (error) {
      message.error(extractErrorMessage(error, "接管项目加载失败"));
    } finally {
      setLoading(false);
    }
  };

  const loadProjectDetails = async (project: TakeoverProject) => {
    try {
      const [readinessResponse, importsResponse, routesResponse] =
        await Promise.all([
          api.get<ReadinessSnapshot>(`/takeovers/${project.id}/readiness`),
          api.get<PageEnvelope<ImportJob>>(`/takeovers/${project.id}/imports`, {
            params: { page: importPage, page_size: TAKEOVER_PAGE_SIZE },
          }),
          api.get<PageEnvelope<RouteVersion>>(
            `/takeovers/${project.id}/routes`,
            {
              params: { page: routePage, page_size: TAKEOVER_PAGE_SIZE },
            }
          ),
        ]);
      setReadiness(readinessResponse.data);
      setImports(importsResponse.data.items || []);
      setImportTotal(importsResponse.data.total || 0);
      setRoutes(routesResponse.data.items || []);
      setRouteTotal(routesResponse.data.total || 0);
      if (access.canAudit) {
        const { data } = await api.get<{
          events: TakeoverEvent[];
          observations: TakeoverObservation[];
          events_total: number;
          observations_total: number;
        }>(`/takeovers/${project.id}/events`, {
          params: { page: auditPage, page_size: TAKEOVER_PAGE_SIZE },
        });
        setEvents(data.events || []);
        setObservations(data.observations || []);
        setEventTotal(data.events_total || 0);
        setObservationTotal(data.observations_total || 0);
      }
    } catch (error) {
      message.error(extractErrorMessage(error, "接管项目详情加载失败"));
    }
  };

  useEffect(() => {
    if (access.canRead) void loadProjects();
    // 项目切换由下方 effect 负责详情加载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.canRead, projectPage]);

  useEffect(() => {
    if (access.canRead && selected) void loadProjectDetails(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.canRead, selected?.id, importPage, routePage, auditPage]);

  const createProject = async (values: Record<string, unknown>) => {
    if (!access.canPrepare || planReadOnly) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<TakeoverProject>("/takeovers", {
        ...values,
        mode: values.mode,
        url_rule: { kind: values.url_rule_kind || "path_tail" },
        control_facts: {
          domain_control: values.mode === "cname",
          old_system_control: values.mode === "legacy_redirect",
          product_mapping: true,
          batch_mapping: true,
        },
      });
      setCreateOpen(false);
      createForm.resetFields();
      setProjects((current) => [data, ...current]);
      selectProject(data);
      message.success("接管项目已创建，下一步请先做旧码导入预检");
    } catch (error) {
      message.error(extractErrorMessage(error, "接管项目创建失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const dryRun = async (file: RcFile) => {
    if (!selected || !access.canPrepare || planReadOnly) return false;
    setActionLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post<ImportJob>(
        `/takeovers/${selected.id}/imports/dry-run`,
        formData,
        {
          headers: { "Content-Type": "multipart/form-data" },
        }
      );
      message.success(
        `预检完成：${data.counts.valid} 行可导入，${data.counts.failed} 行需要修复`
      );
      await loadProjectDetails(selected);
    } catch (error) {
      message.error(extractErrorMessage(error, "导入预检失败"));
    } finally {
      setActionLoading(false);
    }
    return false;
  };

  const submitImport = async (job: ImportJob) => {
    if (!selected || !access.canPrepare || planReadOnly) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<ImportJob>(
        `/takeovers/${selected.id}/imports/${job.id}/submit`
      );
      message.success(
        data.status === "completed"
          ? "正式导入完成，旧码仍保持 staged，未改变消费者流量"
          : "正式导入已排队，处理完成前不会改变消费者流量"
      );
      await loadProjectDetails(selected);
    } catch (error) {
      message.error(extractErrorMessage(error, "正式导入失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const checkDomain = async () => {
    if (!selected || !access.canPrepare || planReadOnly) return;
    setActionLoading(true);
    try {
      const { data } = await api.post(
        `/takeovers/${selected.id}/domains/check`
      );
      message[data.status === "passed" ? "success" : "warning"](
        data.status === "passed"
          ? "DNS、CNAME 与 HTTPS 核验通过"
          : data.failure_reason || "外部域名仍未就绪"
      );
      await loadProjectDetails(selected);
    } catch (error) {
      message.error(extractErrorMessage(error, "域名核验失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const confirmProject = async () => {
    if (!selected || !access.canApprove || planReadOnly) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<TakeoverProject>(
        `/takeovers/${selected.id}/confirm`
      );
      selectProject(data);
      message.success("品牌确认已记录，内容摘要已冻结");
      await loadProjects();
      await loadProjectDetails(data);
    } catch (error) {
      message.error(extractErrorMessage(error, "当前仍有阻塞项，不能确认"));
    } finally {
      setActionLoading(false);
    }
  };

  const createRoute = async (values: Record<string, unknown>) => {
    if (!selected || !access.canPrepare || planReadOnly) return;
    setActionLoading(true);
    try {
      await api.post(`/takeovers/${selected.id}/routes`, {
        source_url: values.source_url,
        target_url: values.target_url,
        sample_codes: String(values.sample_codes || "")
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      });
      setRouteOpen(false);
      routeForm.resetFields();
      message.success("路由候选版本已创建，请完成品牌确认后切换");
      await loadProjectDetails(selected);
    } catch (error) {
      message.error(extractErrorMessage(error, "路由候选创建失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const refreshRouteState = async () => {
    if (!selected) return;
    await loadProjects();
    await loadProjectDetails(selected);
  };

  const confirmRouteVersion = async (route: RouteVersion) => {
    if (!selected || !access.canApprove || planReadOnly) return;
    setActionLoading(true);
    try {
      await api.post(
        `/takeovers/${selected.id}/routes/${route.id}/confirm`,
        null,
        { params: { idempotency_key: crypto.randomUUID() } }
      );
      message.success(`路由 v${route.version} 已确认`);
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "路由确认失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const cutoverRouteVersion = (route: RouteVersion) => {
    if (!selected || !access.canExecute || planReadOnly) return;
    Modal.confirm({
      title: `执行路由 v${route.version} 切换？`,
      content: "仅在品牌确认和外部执行证据均满足后继续；系统会保留幂等记录。",
      okText: "确认切换",
      cancelText: "取消",
      onOk: async () => {
        try {
          await api.post(
            `/takeovers/${selected.id}/routes/${route.id}/cutover`,
            null,
            {
              params: { idempotency_key: crypto.randomUUID() },
            }
          );
          message.success("路由已进入观察期");
          await refreshRouteState();
        } catch (error) {
          message.error(extractErrorMessage(error, "路由切换失败"));
        }
      },
    });
  };

  const submitExternalExecution = async (values: Record<string, unknown>) => {
    if (!selected || !externalRoute || !access.canExecute || planReadOnly)
      return;
    setActionLoading(true);
    try {
      await api.post(
        `/takeovers/${selected.id}/routes/${externalRoute.id}/external-execution`,
        { ...values, idempotency_key: crypto.randomUUID() }
      );
      setExternalRoute(null);
      externalForm.resetFields();
      message.success("外部执行证据已记录，等待真实链接探测");
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "外部执行记录失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const submitProbe = async (values: Record<string, unknown>) => {
    if (!selected || !probeRoute || !access.canExecute || planReadOnly) return;
    setActionLoading(true);
    try {
      await api.post(
        `/takeovers/${selected.id}/routes/${probeRoute.id}/probe`,
        { checked_url: values.checked_url }
      );
      setProbeRoute(null);
      probeForm.resetFields();
      message.success("真实探测指标已记录");
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "探测结果记录失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const submitRollback = async (values: Record<string, unknown>) => {
    if (!selected || !rollbackRoute || !access.canRollback || planReadOnly)
      return;
    setActionLoading(true);
    try {
      await api.post(
        `/takeovers/${selected.id}/routes/${rollbackRoute.id}/rollback`,
        {
          ...values,
          idempotency_key: crypto.randomUUID(),
        }
      );
      setRollbackRoute(null);
      rollbackForm.resetFields();
      message.success("回退已记录，旧链路恢复结果需要重新验证");
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "路由回退失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const completeRouteVersion = async (route: RouteVersion) => {
    if (!selected || !access.canExecute || planReadOnly) return;
    setActionLoading(true);
    try {
      await api.post(
        `/takeovers/${selected.id}/routes/${route.id}/complete`,
        null,
        { params: { idempotency_key: crypto.randomUUID() } }
      );
      message.success("当前切换轮次已完成");
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "完成接管失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const verifyRollback = async (route: RouteVersion) => {
    if (!selected || !access.canRollback || planReadOnly) return;
    setActionLoading(true);
    try {
      const { data } = await api.post<{
        route: RouteVersion;
        observation: TakeoverObservation;
      }>(`/takeovers/${selected.id}/routes/${route.id}/rollback/verify`, null, {
        params: { idempotency_key: crypto.randomUUID() },
      });
      message[data.observation.status === "passed" ? "success" : "warning"](
        data.observation.status === "passed"
          ? "旧入口已验证回到回退目标"
          : "旧入口尚未回到回退目标，请修复后重试验证"
      );
      await refreshRouteState();
    } catch (error) {
      message.error(extractErrorMessage(error, "回退验证失败"));
    } finally {
      setActionLoading(false);
    }
  };

  const routeColumns: ColumnsType<RouteVersion> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (value: number) => `v${value}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value: string) => (
        <Tag color={statusColor(value)}>{STATUS_LABELS[value] || value}</Tag>
      ),
    },
    {
      title: "灰度范围",
      dataIndex: "sample_codes",
      key: "sample_codes",
      render: (value: string[]) => value.join("、") || "全量",
    },
    {
      title: "内容摘要",
      dataIndex: "content_digest",
      key: "content_digest",
      render: (value: string) => value.slice(0, 12),
    },
    {
      title: "操作",
      key: "action",
      render: (_, record) => (
        <Space wrap size={0}>
          {access.canApprove && record.status === "candidate" && (
            <Button
              type="link"
              size="small"
              loading={actionLoading}
              disabled={planReadOnly}
              onClick={() => void confirmRouteVersion(record)}
            >
              确认版本
            </Button>
          )}
          {access.canExecute &&
            selected?.mode === "legacy_redirect" &&
            record.status === "confirmed" && (
              <Button
                type="link"
                size="small"
                disabled={planReadOnly}
                onClick={() => setExternalRoute(record)}
              >
                记录外部执行
              </Button>
            )}
          {access.canExecute && record.status === "confirmed" && (
            <Button
              type="link"
              size="small"
              disabled={
                planReadOnly ||
                (selected?.mode === "legacy_redirect" &&
                  !(
                    selected.status === "pending_external" &&
                    observations.some(
                      (observation) =>
                        observation.evidence_purpose === "pre_cutover" &&
                        observation.status === "passed" &&
                        observation.target_match &&
                        observation.transition_event_id ===
                          record.current_event_id
                    )
                  ))
              }
              onClick={() => cutoverRouteVersion(record)}
            >
              执行切换
            </Button>
          )}
          {access.canExecute &&
            (record.status === "active" ||
              (record.status === "confirmed" &&
                selected?.status === "pending_external")) && (
              <Button
                type="link"
                size="small"
                disabled={planReadOnly}
                onClick={() => setProbeRoute(record)}
              >
                记录探测
              </Button>
            )}
          {access.canExecute && record.status === "active" && (
            <Button
              type="link"
              size="small"
              disabled={planReadOnly}
              onClick={() => void completeRouteVersion(record)}
            >
              完成接管
            </Button>
          )}
          {access.canRollback &&
            ["active", "paused"].includes(record.status) && (
              <Button
                type="link"
                size="small"
                danger
                disabled={planReadOnly}
                onClick={() => setRollbackRoute(record)}
              >
                回退
              </Button>
            )}
          {access.canRollback && record.status === "rolling_back" && (
            <Button
              type="link"
              size="small"
              disabled={planReadOnly}
              onClick={() => void verifyRollback(record)}
            >
              验证回退
            </Button>
          )}
        </Space>
      ),
    },
  ];

  if (!access.canRead) {
    return <Alert type="info" showIcon message="当前账户不能访问既有码接管" />;
  }

  if (loading)
    return (
      <div className="flex justify-center p-12">
        <Spin />
      </div>
    );

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <Title level={4} className="!mb-1">
            既有码接管
          </Title>
          <Paragraph type="secondary" className="!mb-0">
            把旧码接入一码通前，先完成事实评估、导入预检、真实链接验证和可回退的切换计划。
          </Paragraph>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void loadProjects()}>
            刷新
          </Button>
          {access.canPrepare && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={planReadOnly}
              onClick={() => setCreateOpen(true)}
            >
              新建接管项目
            </Button>
          )}
        </Space>
      </div>

      {!projects.length ? (
        <Card>
          <Empty description="还没有接管项目，先创建一个基准客户场景" />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={8}>
            <Card title="项目列表" bodyStyle={{ padding: 0 }}>
              <Table
                size="small"
                showHeader={false}
                pagination={{
                  current: projectPage,
                  pageSize: TAKEOVER_PAGE_SIZE,
                  total: projectTotal,
                  showSizeChanger: false,
                  onChange: setProjectPage,
                }}
                rowKey="id"
                dataSource={projects}
                rowClassName={(record) =>
                  record.id === selected?.id ? "bg-blue-50" : ""
                }
                onRow={(record) => ({
                  onClick: () => selectProject(record),
                  style: { cursor: "pointer" },
                })}
                columns={[
                  {
                    title: "项目",
                    key: "project",
                    render: (_, record) => (
                      <div className="px-3 py-2">
                        <div className="font-medium">{record.name}</div>
                        <Text type="secondary" className="text-xs">
                          {record.source_system} ·{" "}
                          {record.mode === "cname"
                            ? "CNAME 接管"
                            : "旧系统跳转"}
                        </Text>
                        <div>
                          <Tag color={statusColor(record.status)}>
                            {STATUS_LABELS[record.status] || record.status}
                          </Tag>
                        </div>
                      </div>
                    ),
                  },
                ]}
              />
            </Card>
          </Col>
          <Col xs={24} lg={16}>
            {selected && (
              <Space direction="vertical" size="middle" className="w-full">
                <Card
                  title={selected.name}
                  extra={
                    <Tag color={statusColor(selected.status)}>
                      {STATUS_LABELS[selected.status] || selected.status}
                    </Tag>
                  }
                >
                  <Space wrap>
                    <Tag>
                      {selected.assessment.code_type === "unique"
                        ? "一物一码"
                        : "固定/共享链接"}
                    </Tag>
                    <Tag>
                      {selected.mode === "cname"
                        ? "CNAME 域名网关"
                        : "旧系统中转跳转"}
                    </Tag>
                    <Text type="secondary">来源：{selected.source_system}</Text>
                  </Space>
                  <Paragraph className="mt-3">
                    {selected.assessment.recommendation_reason}
                  </Paragraph>
                  {selected.mode === "cname" &&
                    selected.domain_verification_record_name && (
                      <Alert
                        className="mt-3"
                        type="info"
                        showIcon
                        message="域名所有权验证"
                        description={
                          <Space direction="vertical" size={2}>
                            <Text>请添加 TXT 记录后再核验：</Text>
                            <Text code>
                              {selected.domain_verification_record_name}
                            </Text>
                            <Text code>
                              {selected.domain_verification_record_value}
                            </Text>
                          </Space>
                        }
                      />
                    )}
                  <Space wrap>
                    {Object.entries(selected.assessment.capabilities).map(
                      ([key, value]) => (
                        <Tag
                          key={key}
                          color={
                            value.level === "full"
                              ? STATUS_COLORS.success
                              : value.level === "degraded"
                                ? STATUS_COLORS.warning
                                : STATUS_COLORS.error
                          }
                        >
                          {CAPABILITY_LABELS[key] || key}：
                          {value.level === "full"
                            ? "完整"
                            : value.level === "degraded"
                              ? "降级"
                              : "不支持"}
                        </Tag>
                      )
                    )}
                  </Space>
                </Card>

                <Card
                  title="切换准备度"
                  extra={
                    <Space>
                      {access.canPrepare && selected.mode === "cname" && (
                        <Button
                          icon={<GlobalOutlined />}
                          loading={actionLoading}
                          disabled={planReadOnly}
                          onClick={() => void checkDomain()}
                        >
                          重新核验域名
                        </Button>
                      )}
                      {access.canApprove && (
                        <Button
                          icon={<SafetyCertificateOutlined />}
                          loading={actionLoading}
                          disabled={planReadOnly || !readiness?.ready}
                          onClick={() => void confirmProject()}
                        >
                          品牌确认
                        </Button>
                      )}
                    </Space>
                  }
                >
                  {readiness ? (
                    <>
                      <Progress
                        percent={
                          readiness.total_count
                            ? Math.round(
                                (readiness.passed_count /
                                  readiness.total_count) *
                                  100
                              )
                            : 0
                        }
                        status={readiness.ready ? "success" : "active"}
                      />
                      <div className="mt-3 grid gap-2 md:grid-cols-2">
                        {readiness.checks.map((check) => (
                          <Alert
                            key={check.key}
                            type={check.passed ? "success" : "warning"}
                            showIcon
                            icon={
                              check.passed ? <CheckCircleOutlined /> : undefined
                            }
                            message={check.label}
                            description={check.detail}
                          />
                        ))}
                      </div>
                    </>
                  ) : (
                    <Spin />
                  )}
                </Card>

                <Card
                  title="旧码导入"
                  extra={
                    access.canPrepare ? (
                      <Upload
                        beforeUpload={dryRun}
                        showUploadList={false}
                        accept=".csv"
                      >
                        <Button
                          icon={<CloudUploadOutlined />}
                          loading={actionLoading}
                          disabled={planReadOnly}
                        >
                          上传 CSV 做 Dry-run
                        </Button>
                      </Upload>
                    ) : null
                  }
                >
                  <Paragraph type="secondary">
                    模板字段：legacy_code、internal_public_id、sku_code、batch_code。Dry-run
                    不会建立正式别名，也不会改变消费者流量。
                  </Paragraph>
                  <Table
                    size="small"
                    rowKey="id"
                    pagination={{
                      current: importPage,
                      pageSize: TAKEOVER_PAGE_SIZE,
                      total: importTotal,
                      showSizeChanger: false,
                      onChange: setImportPage,
                    }}
                    dataSource={imports}
                    columns={[
                      {
                        title: "文件",
                        dataIndex: "file_name",
                        key: "file_name",
                      },
                      {
                        title: "状态",
                        dataIndex: "status",
                        key: "status",
                        render: (value: string) => (
                          <Tag color={statusColor(value)}>{value}</Tag>
                        ),
                      },
                      {
                        title: "总数",
                        key: "total",
                        render: (_, record) => record.counts.total,
                      },
                      {
                        title: "可导入",
                        key: "valid",
                        render: (_, record) => record.counts.valid,
                      },
                      {
                        title: "失败",
                        key: "failed",
                        render: (_, record) => record.counts.failed,
                      },
                      {
                        title: "操作",
                        key: "action",
                        render: (_, record) =>
                          access.canPrepare &&
                          record.status === "dry_run" &&
                          record.counts.failed === 0 ? (
                            <Button
                              type="link"
                              disabled={planReadOnly}
                              onClick={() => void submitImport(record)}
                            >
                              确认正式导入
                            </Button>
                          ) : null,
                      },
                    ]}
                  />
                </Card>

                <Card
                  title="路由版本"
                  extra={
                    access.canPrepare ? (
                      <Button
                        icon={<PlusOutlined />}
                        disabled={planReadOnly}
                        onClick={() => setRouteOpen(true)}
                      >
                        创建候选版本
                      </Button>
                    ) : null
                  }
                >
                  {selected.mode === "legacy_redirect" && (
                    <Alert
                      className="mb-3"
                      type="info"
                      message="旧系统跳转由客户技术负责人在外部执行，平台会显示待外部执行，并要求真实链接探测后才进入观察。"
                    />
                  )}
                  <Table
                    size="small"
                    rowKey="id"
                    pagination={{
                      current: routePage,
                      pageSize: TAKEOVER_PAGE_SIZE,
                      total: routeTotal,
                      showSizeChanger: false,
                      onChange: setRoutePage,
                    }}
                    dataSource={routes}
                    columns={routeColumns}
                  />
                </Card>
                {access.canAudit && (
                  <Card title="切换事件与服务端探测">
                    <Table
                      size="small"
                      rowKey="id"
                      pagination={{
                        current: auditPage,
                        pageSize: TAKEOVER_PAGE_SIZE,
                        total: eventTotal,
                        showSizeChanger: false,
                        onChange: setAuditPage,
                      }}
                      dataSource={events}
                      columns={[
                        { title: "动作", dataIndex: "action", key: "action" },
                        { title: "状态", dataIndex: "state", key: "state" },
                        {
                          title: "时间",
                          dataIndex: "created_at",
                          key: "created_at",
                        },
                      ]}
                    />
                    <Divider />
                    <Table
                      size="small"
                      rowKey="id"
                      pagination={{
                        current: auditPage,
                        pageSize: TAKEOVER_PAGE_SIZE,
                        total: observationTotal,
                        showSizeChanger: false,
                        onChange: setAuditPage,
                      }}
                      dataSource={observations}
                      columns={[
                        {
                          title: "阶段",
                          dataIndex: "evidence_purpose",
                          key: "purpose",
                        },
                        {
                          title: "旧入口",
                          dataIndex: "checked_url",
                          key: "checked_url",
                        },
                        {
                          title: "结果",
                          key: "status",
                          render: (_, record) => (
                            <Tag
                              color={
                                record.target_match
                                  ? STATUS_COLORS.success
                                  : STATUS_COLORS.error
                              }
                            >
                              {record.status}
                            </Tag>
                          ),
                        },
                      ]}
                    />
                  </Card>
                )}
              </Space>
            )}
          </Col>
        </Row>
      )}

      <Modal
        title="新建既有码接管项目"
        open={createOpen}
        confirmLoading={actionLoading}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void createForm.submit()}
        okText="创建项目"
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={createProject}
          initialValues={{
            mode: "legacy_redirect",
            url_rule_kind: "path_tail",
          }}
        >
          <Form.Item
            name="name"
            label="项目名称"
            rules={[{ required: true, message: "请输入项目名称" }]}
          >
            <Input placeholder="例如：2026 春茶旧包装接管" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="source_system"
                label="旧码来源系统"
                rules={[{ required: true }]}
              >
                <Input placeholder="旧防伪系统" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="mode"
                label="接管模式"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: "legacy_redirect", label: "旧系统中转跳转" },
                    { value: "cname", label: "CNAME 域名网关" },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="source_domain" label="旧系统域名">
                <Input placeholder="legacy.example.com" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="consumer_domain" label="消费者扫码域名">
                <Input placeholder="scan.brand.example.com（CNAME 必填）" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="sample_url"
            label="真实旧链接样本"
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="https://legacy.example.com/scan/OLD-001" />
          </Form.Item>
          <Form.Item
            name="fallback_url"
            label="独立回退目标"
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="https://legacy-origin.example.com/fallback" />
          </Form.Item>
          <Form.Item name="url_rule_kind" label="取码规则">
            <Select
              options={[
                { value: "path_tail", label: "路径最后一段" },
                { value: "query", label: "指定 query 参数" },
                { value: "fixed", label: "固定/共享链接" },
              ]}
            />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="responsible_person"
                label="业务负责人"
                rules={[{ required: true }]}
              >
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="rollback_contact"
                label="回退联系人"
                rules={[{ required: true }]}
              >
                <Input />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title="创建路由候选版本"
        open={routeOpen}
        confirmLoading={actionLoading}
        onCancel={() => setRouteOpen(false)}
        onOk={() => void routeForm.submit()}
        okText="保存候选版本"
      >
        <Form form={routeForm} layout="vertical" onFinish={createRoute}>
          <Form.Item
            name="source_url"
            label="旧入口 URL"
            rules={[{ required: true, type: "url" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="target_url"
            label="新链路目标"
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="https://h5.example.com/c/{public_id}（可绑定内部业务码）" />
          </Form.Item>
          <Form.Item name="sample_codes" label="样本旧码（逗号分隔）">
            <Input placeholder="OLD-001, OLD-002；留空表示全量" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="记录旧系统外部执行"
        open={Boolean(externalRoute)}
        confirmLoading={actionLoading}
        onCancel={() => setExternalRoute(null)}
        onOk={() => void externalForm.submit()}
        okText="记录证据"
      >
        <Alert
          className="mb-3"
          type="warning"
          showIcon
          message="这里只记录客户技术负责人已完成的外部动作，不会代替真实旧链接探测。"
        />
        <Form
          form={externalForm}
          layout="vertical"
          onFinish={submitExternalExecution}
        >
          <Form.Item
            name="execution_reference"
            label="外部变更单号/证据编号"
            rules={[{ required: true, message: "请输入可追溯的外部证据编号" }]}
          >
            <Input placeholder="例如：legacy-change-20260801-001" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="记录真实旧链接探测"
        open={Boolean(probeRoute)}
        confirmLoading={actionLoading}
        onCancel={() => setProbeRoute(null)}
        onOk={() => void probeForm.submit()}
        okText="保存探测结果"
      >
        <Form form={probeForm} layout="vertical" onFinish={submitProbe}>
          <Form.Item
            name="checked_url"
            label="实际探测 URL"
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="https://legacy.example.com/scan/OLD-001" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="记录路由回退"
        open={Boolean(rollbackRoute)}
        confirmLoading={actionLoading}
        onCancel={() => setRollbackRoute(null)}
        onOk={() => void rollbackForm.submit()}
        okText="确认回退"
        okButtonProps={{ danger: true }}
      >
        <Alert
          className="mb-3"
          type="error"
          showIcon
          message="回退不会删除映射和审计记录；提交后仍需通过真实旧链接验证恢复结果。"
        />
        <Form form={rollbackForm} layout="vertical" onFinish={submitRollback}>
          <Form.Item
            name="reason"
            label="回退原因"
            rules={[{ required: true, message: "请填写回退原因" }]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>

      <Divider className="!my-4" />
      <Text type="secondary">
        接管状态只在真实事实、外部执行证据和可验证回退均满足时推进；固定链接能力会按实际映射自动降级。
      </Text>
    </div>
  );
}
