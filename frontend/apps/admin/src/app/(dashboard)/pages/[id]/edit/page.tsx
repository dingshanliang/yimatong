"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  Alert,
  App,
  Collapse,
  Descriptions,
  Input,
  List,
  Tabs,
  Typography,
} from "antd";
import api from "@/lib/api";
import {
  inspectPageReadiness,
  validateDSL,
  createEmptyDSL,
  type PageDSL,
  type PagePreviewContext,
} from "@/lib/page-dsl";
import { EditorHeader } from "./components/EditorHeader";
import { ModuleList } from "./components/ModuleList";
import { RoutingConfig } from "./components/RoutingConfig";
import { PreviewPanel } from "./components/PreviewPanel";

const { TextArea } = Input;
const { Text } = Typography;

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

export default function PageEditorPage() {
  const params = useParams<{ id: string }>();
  const { message, modal } = App.useApp();

  const [templateName, setTemplateName] = useState("");
  const [previewContext, setPreviewContext] = useState<PagePreviewContext>({});
  const [version, setVersion] = useState<PageVersion | null>(null);
  const [dsl, setDsl] = useState<PageDSL>(createEmptyDSL());
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("modules");
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const { data: tpl } = await api.get(`/page-templates/${params.id}`);
        setTemplateName(tpl.name);
        const { data: versions } = await api.get(
          `/page-templates/${params.id}/versions`
        );
        const draft = versions.find((v: PageVersion) => v.status === "draft");
        const target = draft || versions[0];
        const nextDsl = (target?.config_json as PageDSL) || createEmptyDSL();

        if (tpl.product_id) {
          const { data: productList } = await api.get("/products", {
            params: { page_size: 100 },
          });
          const product =
            (productList.items || []).find(
              (item: { id: string }) => item.id === tpl.product_id
            ) || null;
          const modules = nextDsl.modules || [];
          const needsBatches = modules.some(
            (module) =>
              module.enabled !== false && module.type === "light_traceability"
          );
          const needsAssets = modules.some(
            (module) =>
              module.enabled !== false &&
              ["test_reports", "certificates", "media_section"].includes(
                module.type
              )
          );
          const [batchResult, assetResult] = product
            ? await Promise.allSettled([
                needsBatches
                  ? api.get(`/products/${tpl.product_id}/batches`, {
                      params: { page_size: 100 },
                    })
                  : Promise.resolve({ data: { items: [] } }),
                needsAssets
                  ? api.get(`/products/${tpl.product_id}/assets`, {
                      params: { page_size: 100 },
                    })
                  : Promise.resolve({ data: { items: [] } }),
              ])
            : [];
          setPreviewContext({
            product,
            batches:
              batchResult?.status === "fulfilled"
                ? [...(batchResult.value.data.items || [])].sort(
                    (
                      a: { production_date?: string },
                      b: { production_date?: string }
                    ) =>
                      String(b.production_date || "").localeCompare(
                        String(a.production_date || "")
                      )
                  )
                : [],
            assets:
              assetResult?.status === "fulfilled"
                ? assetResult.value.data.items || []
                : [],
          });
        } else {
          setPreviewContext({});
        }
        if (target) {
          setVersion(target);
          setDsl(nextDsl);
          setSelectedModuleId(nextDsl.modules?.[0]?.id || null);
        }
      } catch {
        message.error("加载页面数据失败");
      }
    }
    load();
  }, [params.id, message]);

  const readiness = inspectPageReadiness(dsl, previewContext);
  const enabledModuleCount = (dsl.modules || []).filter(
    (module) => module.enabled !== false
  ).length;
  const issueCount =
    readiness.blockingIssues.length + readiness.warnings.length;

  const handleSave = useCallback(async () => {
    if (!version) return;
    const errors = validateDSL(dsl);
    if (errors.length > 0) {
      message.error(`DSL 校验失败: ${errors[0]}`);
      return;
    }
    try {
      setSaving(true);
      await api.patch(`/page-versions/${version.id}`, { config_json: dsl });
      message.success("保存成功");
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  }, [version, dsl, message]);

  const refreshData = useCallback(async () => {
    try {
      const { data: versions } = await api.get(
        `/page-templates/${params.id}/versions`
      );
      const draft = versions.find((v: PageVersion) => v.status === "draft");
      const target = draft || versions[0];
      if (target) {
        setVersion(target);
        const nextDsl = (target.config_json as PageDSL) || createEmptyDSL();
        setDsl(nextDsl);
        setSelectedModuleId(nextDsl.modules?.[0]?.id || null);
      }
    } catch {
      /* ignore */
    }
  }, [params.id]);

  const handlePublish = useCallback(() => {
    if (!version) return;
    const currentReadiness = inspectPageReadiness(dsl, previewContext);
    if (currentReadiness.blockingIssues.length > 0) {
      modal.warning({
        title: "草稿暂不能发布",
        content: (
          <div className="space-y-3">
            <Alert
              type="warning"
              showIcon
              title="请先处理以下阻断项，再发布给消费者。"
            />
            <List
              size="small"
              dataSource={currentReadiness.blockingIssues}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
          </div>
        ),
        okText: "知道了",
      });
      return;
    }

    modal.confirm({
      title: "发布此页面草稿？",
      width: 620,
      okText: "发布草稿",
      cancelText: "取消",
      content: (
        <div className="space-y-3">
          <Descriptions size="small" bordered column={1}>
            <Descriptions.Item label="页面">{templateName}</Descriptions.Item>
            <Descriptions.Item label="关联产品">
              {previewContext.product?.name || "未关联产品"}
            </Descriptions.Item>
            <Descriptions.Item label="草稿版本">
              v{version.version}
            </Descriptions.Item>
            <Descriptions.Item label="模块">
              {enabledModuleCount} 个已启用模块
            </Descriptions.Item>
          </Descriptions>
          {currentReadiness.warnings.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              title="发布前请确认这些风险"
              description={
                <ul className="m-0 pl-4">
                  {currentReadiness.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          ) : (
            <Alert type="success" showIcon title="发布检查通过" />
          )}
          <Text type="secondary">
            发布后消费者扫码可能看到此页面。系统会先保存当前草稿再发布。
          </Text>
        </div>
      ),
      onOk: async () => {
        try {
          setSaving(true);
          await api.patch(`/page-versions/${version.id}`, { config_json: dsl });
          await api.post(`/page-versions/${version.id}/publish`);
          message.success("草稿已发布，消费者扫码可能看到此页面");
          await refreshData();
        } catch {
          message.error("发布失败");
        } finally {
          setSaving(false);
        }
      },
    });
  }, [
    dsl,
    enabledModuleCount,
    message,
    modal,
    previewContext,
    refreshData,
    templateName,
    version,
  ]);

  if (!version) {
    return (
      <div className="flex h-screen items-center justify-center text-text-muted">
        加载中...
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <EditorHeader
        templateId={params.id}
        templateName={templateName}
        version={version.version}
        versionStatus={version.status}
        productName={previewContext.product?.name || null}
        moduleCount={enabledModuleCount}
        issueCount={issueCount}
        saving={saving}
        onSave={handleSave}
        onPublish={handlePublish}
      />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-130 shrink-0 overflow-y-auto border-r bg-bg-container p-4">
          {readiness.blockingIssues.length > 0 ? (
            <Alert
              className="mb-3"
              type="warning"
              showIcon
              title={`${readiness.blockingIssues.length} 项阻断发布`}
              description={readiness.blockingIssues[0]}
            />
          ) : readiness.warnings.length > 0 ? (
            <Alert
              className="mb-3"
              type="info"
              showIcon
              title={`${readiness.warnings.length} 项发布前确认`}
              description={readiness.warnings[0]}
            />
          ) : null}
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={[
              {
                key: "modules",
                label: "模块配置",
                children: (
                  <ModuleList
                    modules={dsl.modules || []}
                    productId={previewContext.product?.id || null}
                    moduleStatuses={readiness.moduleStatuses}
                    selectedModuleId={selectedModuleId}
                    onSelectModule={setSelectedModuleId}
                    onChange={(modules) => setDsl({ ...dsl, modules })}
                  />
                ),
              },
              {
                key: "routing",
                label: "活动期配置",
                children: <RoutingConfig dsl={dsl} onChange={setDsl} />,
              },
            ]}
          />
          <Collapse
            className="mt-4"
            size="small"
            items={[
              {
                key: "json",
                label: "高级配置",
                children: (
                  <div>
                    <Alert
                      className="mb-3"
                      type="info"
                      showIcon
                      title="仅供技术人员排查或批量调整 DSL 使用"
                    />
                    <JSONEditor dsl={dsl} onChange={setDsl} />
                  </div>
                ),
              },
            ]}
          />
        </div>
        <div className="flex-1 overflow-hidden bg-bg-muted p-4">
          <PreviewPanel
            dsl={dsl}
            previewContext={previewContext}
            usesExampleData={
              readiness.usesExampleData || !previewContext.product
            }
          />
        </div>
      </div>
    </div>
  );
}

function JSONEditor({
  dsl,
  onChange,
}: {
  dsl: PageDSL;
  onChange: (dsl: PageDSL) => void;
}) {
  const [text, setText] = useState(JSON.stringify(dsl, null, 2));
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    setText(JSON.stringify(dsl, null, 2));
    setErrors(validateDSL(dsl));
  }, [dsl]);

  const handleChange = (value: string) => {
    setText(value);
    try {
      const parsed = JSON.parse(value);
      const errs = validateDSL(parsed);
      setErrors(errs);
      if (errs.length === 0) {
        onChange(parsed as PageDSL);
      }
    } catch (e) {
      setErrors([`JSON 语法错误: ${(e as Error).message}`]);
    }
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-text-muted">直接编辑 JSON 配置</span>
        {errors.length > 0 ? (
          <span
            className="text-xs"
            style={{ color: "var(--ymt-color-feedback-danger)" }}
          >
            {errors.length} 个错误
          </span>
        ) : (
          <span
            className="text-xs"
            style={{ color: "var(--ymt-color-feedback-success)" }}
          >
            校验通过
          </span>
        )}
      </div>
      <TextArea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={20}
        className="font-mono text-sm"
      />
      {errors.length > 0 && (
        <div
          className="mt-2 rounded p-3"
          style={{ background: "var(--ymt-color-feedback-danger-bg)" }}
        >
          {errors.map((err, i) => (
            <div
              key={i}
              className="text-xs"
              style={{ color: "var(--ymt-color-feedback-danger)" }}
            >
              {err}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
