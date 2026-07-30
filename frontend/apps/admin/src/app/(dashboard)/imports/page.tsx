"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  DownloadOutlined,
  InboxOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

const { Title, Text } = Typography;
const { Dragger } = Upload;

interface ImportRecord {
  id: string;
  file_name: string;
  status: "pending" | "processing" | "completed" | "failed";
  total_rows: number;
  created_count: number;
  updated_count: number;
  failed_count: number;
  error_detail?: string;
  created_at: string;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "等待中", color: "default" },
  processing: { label: "处理中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  failed: { label: "失败", color: "red" },
};

function formatDate(dateStr?: string): string {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN");
}

export default function ImportsPage() {
  const { message } = App.useApp();
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [errorModal, setErrorModal] = useState<{
    open: boolean;
    title: string;
    detail: string;
  }>({ open: false, title: "", detail: "" });

  const fetchRecords = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/imports/records");
      setRecords(Array.isArray(data) ? data : data.items || []);
    } catch {
      // 接口尚未就绪时显示空列表
      setRecords([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRecords();
  }, [fetchRecords]);

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
    action: `${process.env.NEXT_PUBLIC_API_URL || ""}/api/v1/imports/excel`,
    headers: { Authorization: `Bearer ${getToken()}` },
    accept: ".xlsx,.xls",
    onChange(info: {
      file: {
        status?: string;
        name: string;
        response?: { created?: number; updated?: number; detail?: string };
      };
    }) {
      if (info.file.status === "done") {
        const resp = info.file.response;
        message.success(
          `导入完成: ${resp?.created || 0} 条创建, ${resp?.updated || 0} 条更新`
        );
        fetchRecords();
      } else if (info.file.status === "error") {
        message.error("导入失败，请检查文件格式是否正确");
      }
    },
  };

  const handleErrorClick = (record: ImportRecord) => {
    setErrorModal({
      open: true,
      title: `${record.file_name} - 错误详情`,
      detail: record.error_detail || "无详细错误信息",
    });
  };

  const columns: ColumnsType<ImportRecord> = [
    {
      title: "文件名",
      dataIndex: "file_name",
      key: "file_name",
      ellipsis: true,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    { title: "总行数", dataIndex: "total_rows", key: "total_rows" },
    {
      title: "创建",
      dataIndex: "created_count",
      key: "created_count",
      render: (v: number) => (
        <Text
          style={{
            color: v > 0 ? "var(--ymt-color-feedback-success)" : undefined,
          }}
        >
          {v ?? 0}
        </Text>
      ),
    },
    {
      title: "更新",
      dataIndex: "updated_count",
      key: "updated_count",
      render: (v: number) => (
        <Text
          style={{
            color: v > 0 ? "var(--ymt-color-feedback-info)" : undefined,
          }}
        >
          {v ?? 0}
        </Text>
      ),
    },
    {
      title: "失败",
      dataIndex: "failed_count",
      key: "failed_count",
      render: (v: number, record: ImportRecord) =>
        v > 0 ? (
          <Button
            type="link"
            size="small"
            onClick={() => handleErrorClick(record)}
          >
            <Text style={{ color: "var(--ymt-color-feedback-danger)" }}>
              {v}
            </Text>
          </Button>
        ) : (
          <Text>{v ?? 0}</Text>
        ),
    },
    {
      title: "导入时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => formatDate(v),
    },
  ];

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
              支持 .xlsx / .xls 格式，请先下载模板填写数据
            </p>
          </Dragger>
        </Space>
      </Card>

      <Card
        title="导入记录"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchRecords}
            loading={loading}
          >
            刷新
          </Button>
        }
      >
        <Spin spinning={loading}>
          <Table
            columns={columns}
            dataSource={records}
            rowKey="id"
            pagination={{
              pageSize: 20,
              showTotal: (t) => `共 ${t} 条`,
            }}
            locale={{ emptyText: "暂无导入记录" }}
          />
        </Spin>
      </Card>

      <Modal
        open={errorModal.open}
        title={errorModal.title}
        onCancel={() => setErrorModal({ open: false, title: "", detail: "" })}
        footer={
          <Button
            onClick={() =>
              setErrorModal({ open: false, title: "", detail: "" })
            }
          >
            关闭
          </Button>
        }
        width={600}
      >
        <pre
          style={{
            maxHeight: 400,
            overflow: "auto",
            background: "var(--ymt-color-bg-muted)",
            padding: 12,
            borderRadius: "var(--ymt-radius-sm)",
            fontSize: "var(--ymt-font-size-sm)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {errorModal.detail}
        </pre>
      </Modal>
    </div>
  );
}
