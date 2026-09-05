"use client";

import React, { useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Space,
  Typography,
  Upload,
} from "antd";
import { DownloadOutlined, InboxOutlined } from "@ant-design/icons";
import api, { API_BASE_URL } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { catalogAccessForPrincipal } from "@/lib/catalog-access";
import { useTenantPlanReadOnly } from "../_components/TenantPlanReadOnly";

const { Title, Text } = Typography;
const { Dragger } = Upload;

export const EXCEL_UPLOAD_ACTION = `${API_BASE_URL}/api/v1/imports/excel`;
const MAX_SHOWN_IMPORT_ERRORS = 5;

interface ImportRowError {
  sheet?: string;
  row?: number;
  message?: string;
}

interface ImportUploadResponse {
  success?: boolean;
  message?: string;
  created?: number;
  updated?: number;
  errors?: ImportRowError[];
}

interface ImportFailure {
  summary: string;
  backendMessage?: string;
  rows: ImportRowError[];
  totalErrors: number;
}

function formatRowError(error: ImportRowError): string {
  const location = [
    error.sheet ? `「${error.sheet}」` : "",
    typeof error.row === "number" ? `第 ${error.row} 行` : "",
  ]
    .filter(Boolean)
    .join(" ");
  return `${location ? location + "：" : ""}${error.message || "未知错误"}`;
}

export type ImportOutcome =
  | { ok: true; created: number; updated: number }
  | { ok: false; failure: ImportFailure };

/** 把 /imports/excel 的 HTTP 200 响应体归类为成功或失败（含错误行摘要）。 */
export function summarizeImportResponse(
  resp: ImportUploadResponse | undefined
): ImportOutcome {
  if (resp?.success === false) {
    const errors = Array.isArray(resp.errors) ? resp.errors : [];
    const succeeded = (resp.created || 0) + (resp.updated || 0);
    return {
      ok: false,
      failure: {
        summary: `解析完成：成功 ${succeeded} 条，失败 ${errors.length} 条`,
        backendMessage: resp.message,
        rows: errors.slice(0, MAX_SHOWN_IMPORT_ERRORS),
        totalErrors: errors.length,
      },
    };
  }
  return {
    ok: true,
    created: resp?.created || 0,
    updated: resp?.updated || 0,
  };
}

export default function ImportsPage() {
  const user = useAuthStore((state) => state.user);
  const access = catalogAccessForPrincipal(user);

  if (!access.canRead) {
    return <Alert type="warning" showIcon title="当前账号无权访问产品导入" />;
  }

  return <ImportsWorkspace canWrite={access.canWrite} />;
}

function ImportsWorkspace({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp();
  const planReadOnly = useTenantPlanReadOnly();
  const uploadDisabled = planReadOnly || !canWrite;
  const [downloading, setDownloading] = useState(false);
  const [lastFailure, setLastFailure] = useState<ImportFailure | null>(null);

  const handleDownloadTemplate = async () => {
    setDownloading(true);
    try {
      const response = await api.get("/imports/template", {
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", "import_template.xlsx");
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      message.success("模板下载成功");
    } catch {
      message.error("模板下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  };

  const getToken = () => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("access_token") || "";
    }
    return "";
  };

  const uploadProps = {
    name: "file",
    action: EXCEL_UPLOAD_ACTION,
    headers: { Authorization: `Bearer ${getToken()}` },
    accept: ".xlsx",
    disabled: uploadDisabled,
    beforeUpload(file: File) {
      if (uploadDisabled) return Upload.LIST_IGNORE;
      if (!file.name.toLowerCase().endsWith(".xlsx")) {
        message.error("不支持该文件格式，请上传 .xlsx 文件");
        return Upload.LIST_IGNORE;
      }
      return true;
    },
    onChange(info: {
      file: {
        status?: string;
        name: string;
        response?: ImportUploadResponse & { detail?: string };
      };
    }) {
      if (info.file.status === "done") {
        const outcome = summarizeImportResponse(info.file.response);
        // 后端对解析失败/行级错误返回 HTTP 200 + success:false + errors[]，必须显式呈现
        if (!outcome.ok) {
          setLastFailure(outcome.failure);
          return;
        }
        setLastFailure(null);
        message.success(
          `导入完成: ${outcome.created} 条创建, ${outcome.updated} 条更新`
        );
      } else if (info.file.status === "error") {
        setLastFailure(null);
        if (info.file.response?.detail === "Unsupported import file type") {
          message.error("不支持该文件格式，请上传 .xlsx 文件");
        } else {
          message.error("导入失败，请检查文件格式是否正确");
        }
      }
    },
  };

  return (
    <div>
      <Title level={4}>产品导入</Title>

      <Card title="上传文件" style={{ marginBottom: 24 }}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Button
            icon={<DownloadOutlined />}
            onClick={handleDownloadTemplate}
            loading={downloading}
          >
            下载导入模板
          </Button>

          <Dragger {...uploadProps}>
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 Excel 文件到此区域上传</p>
            <p className="ant-upload-hint">
              仅支持 .xlsx 格式，请先下载模板填写数据
            </p>
          </Dragger>

          {lastFailure && (
            <Alert
              type="error"
              showIcon
              title={lastFailure.summary}
              description={
                <div>
                  {lastFailure.backendMessage && (
                    <Text type="secondary">{lastFailure.backendMessage}</Text>
                  )}
                  <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>
                    {lastFailure.rows.map((row, index) => (
                      <li key={index}>{formatRowError(row)}</li>
                    ))}
                  </ul>
                  {lastFailure.totalErrors > lastFailure.rows.length && (
                    <Text type="secondary">
                      …等共 {lastFailure.totalErrors} 条错误，请修正后重新上传
                    </Text>
                  )}
                </div>
              }
              closable
              onClose={() => setLastFailure(null)}
            />
          )}
        </Space>
      </Card>

      <Card title="导入记录">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="导入记录暂不支持保存与回查，导入结果以上传完成后的提示为准"
        />
      </Card>
    </div>
  );
}
