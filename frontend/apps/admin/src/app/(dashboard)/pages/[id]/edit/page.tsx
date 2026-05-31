"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import { App, Tabs, Input } from "antd";
import api from "@/lib/api";
import {
  validateDSL,
  createEmptyDSL,
  type PageDSL,
} from "@/lib/page-dsl";
import { EditorHeader } from "./components/EditorHeader";
import { ModuleList } from "./components/ModuleList";
import { RoutingConfig } from "./components/RoutingConfig";
import { PreviewPanel } from "./components/PreviewPanel";

const { TextArea } = Input;

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
  const { message } = App.useApp();

  const [templateName, setTemplateName] = useState("");
  const [templateProductId, setTemplateProductId] = useState<string | null>(null);
  const [version, setVersion] = useState<PageVersion | null>(null);
  const [dsl, setDsl] = useState<PageDSL>(createEmptyDSL());
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("modules");

  useEffect(() => {
    async function load() {
      try {
        const { data: tpl } = await api.get(`/page-templates/${params.id}`);
        setTemplateName(tpl.name);
        setTemplateProductId(tpl.product_id || null);
        const { data: versions } = await api.get(`/page-templates/${params.id}/versions`);
        const draft = versions.find((v: PageVersion) => v.status === "draft");
        const target = draft || versions[0];
        if (target) {
          setVersion(target);
          setDsl((target.config_json as PageDSL) || createEmptyDSL());
        }
      } catch {
        message.error("加载页面数据失败");
      }
    }
    load();
  }, [params.id, message]);

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
      const { data: versions } = await api.get(`/page-templates/${params.id}/versions`);
      const draft = versions.find((v: PageVersion) => v.status === "draft");
      const target = draft || versions[0];
      if (target) {
        setVersion(target);
        setDsl((target.config_json as PageDSL) || createEmptyDSL());
      }
    } catch { /* ignore */ }
  }, [params.id]);

  if (!version) {
    return <div className="flex h-screen items-center justify-center text-gray-400">加载中...</div>;
  }

  return (
    <div className="flex h-screen flex-col">
      <EditorHeader
        templateId={params.id}
        templateName={templateName}
        version={version.version}
        versionStatus={version.status}
        versionId={version.id}
        saving={saving}
        onSave={handleSave}
        onRefresh={refreshData}
      />
      <div className="flex flex-1 overflow-hidden">
        <div className="w-[480px] shrink-0 overflow-y-auto border-r bg-white p-4">
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={[
              {
                key: "modules",
                label: "模块排序",
                children: (
                  <ModuleList
                    modules={dsl.modules || []}
                    productId={templateProductId}
                    onChange={(modules) => setDsl({ ...dsl, modules })}
                  />
                ),
              },
              {
                key: "routing",
                label: "活动期配置",
                children: <RoutingConfig dsl={dsl} onChange={setDsl} />,
              },
              {
                key: "json",
                label: "JSON 编辑",
                children: <JSONEditor dsl={dsl} onChange={setDsl} />,
              },
            ]}
          />
        </div>
        <div className="flex-1 overflow-hidden bg-gray-50 p-4">
          <PreviewPanel dsl={dsl} />
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
        <span className="text-xs text-gray-500">直接编辑 JSON 配置</span>
        {errors.length > 0 ? (
          <span className="text-xs text-red-500">{errors.length} 个错误</span>
        ) : (
          <span className="text-xs text-green-500">校验通过</span>
        )}
      </div>
      <TextArea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={20}
        className="font-mono text-sm"
      />
      {errors.length > 0 && (
        <div className="mt-2 rounded bg-red-50 p-3">
          {errors.map((err, i) => (
            <div key={i} className="text-xs text-red-600">{err}</div>
          ))}
        </div>
      )}
    </div>
  );
}
