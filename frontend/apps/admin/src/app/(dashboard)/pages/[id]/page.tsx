"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { App, Button, Popconfirm, Space, Table, Tag, Tooltip } from "antd";
import {
  ArrowLeftOutlined,
  EditOutlined,
  SendOutlined,
  StopOutlined,
  RollbackOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import api from "@/lib/api";
import { createEmptyDSL } from "@/lib/page-dsl";

interface PageVersion {
  id: string;
  version: number;
  status: string;
  config_json: Record<string, unknown>;
  published_at?: string;
  created_at: string;
}

const VERSION_STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "#8c8c8c" },
  published: { label: "已发布", color: "#1d4ed8" },
  archived: { label: "已归档", color: "#8c8c8c" },
  offline: { label: "已下线", color: "#f59e0b" },
};

export default function VersionPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { message } = App.useApp();

  const [templateName, setTemplateName] = useState("");
  const [versions, setVersions] = useState<PageVersion[]>([]);

  useEffect(() => {
    async function load() {
      try {
        const { data: tpl } = await api.get(`/page-templates/${params.id}`);
        setTemplateName(tpl.name);
        const { data: vs } = await api.get(
          `/page-templates/${params.id}/versions`
        );
        setVersions(vs);
      } catch {
        message.error("加载失败");
      }
    }
    load();
  }, [params.id, message]);

  const refresh = async () => {
    try {
      const { data: vs } = await api.get(
        `/page-templates/${params.id}/versions`
      );
      setVersions(vs);
    } catch {
      /* ignore */
    }
  };

  const publishVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/publish`);
      message.success("草稿已发布");
      refresh();
    } catch {
      message.error("发布失败");
    }
  };

  const archiveVersion = async (versionId: string) => {
    try {
      await api.post(`/page-versions/${versionId}/archive`);
      message.success("线上版本已下线");
      refresh();
    } catch {
      message.error("下线失败");
    }
  };

  const rollbackVersion = async (versionId: string) => {
    try {
      await api.post(
        `/page-templates/${params.id}/versions/${versionId}/rollback`
      );
      message.success("已基于此版本创建草稿");
      refresh();
    } catch {
      message.error("回滚失败");
    }
  };

  const createNewDraft = async () => {
    try {
      const baseConfig =
        versions.find((v) => v.status === "published")?.config_json ??
        versions[0]?.config_json ??
        createEmptyDSL();
      await api.post(`/page-templates/${params.id}/versions`, {
        config_json: baseConfig,
      });
      message.success("草稿已创建");
      refresh();
    } catch {
      message.error("创建草稿失败");
    }
  };

  const columns: ColumnsType<PageVersion> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: number) => `v${v}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const info = VERSION_STATUS_MAP[s] || { label: s, color: "#8c8c8c" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "发布时间",
      dataIndex: "published_at",
      key: "published_at",
      render: (v: string) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "-"),
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => dayjs(v).format("YYYY-MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: PageVersion) => (
        <Space>
          {record.status === "draft" && (
            <>
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => router.push(`/pages/${params.id}/edit`)}
              >
                编辑草稿
              </Button>
              <Popconfirm
                title="发布此页面草稿？"
                onConfirm={() => publishVersion(record.id)}
              >
                <Button size="small" type="primary" icon={<SendOutlined />}>
                  发布草稿
                </Button>
              </Popconfirm>
            </>
          )}
          {record.status === "published" && (
            <Popconfirm
              title="下线此线上版本？"
              onConfirm={() => archiveVersion(record.id)}
            >
              <Button size="small" danger icon={<StopOutlined />}>
                下线线上版本
              </Button>
            </Popconfirm>
          )}
          {record.status !== "draft" && (
            <Tooltip title="基于此版本创建草稿">
              <Button
                size="small"
                icon={<RollbackOutlined />}
                onClick={() => rollbackVersion(record.id)}
              >
                创建草稿
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/pages")}
          >
            返回
          </Button>
          <h2 className="text-lg font-semibold !mb-0">
            版本记录 — {templateName}
          </h2>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={createNewDraft}>
          创建新草稿
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={versions}
        rowKey="id"
        pagination={false}
        size="middle"
      />
    </div>
  );
}
