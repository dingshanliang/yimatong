"use client";

import { useState, useEffect } from "react";
import { List, Checkbox, Card, Progress, Typography, Tag } from "antd";

const { Title } = Typography;

interface ChecklistItem {
  key: string;
  label: string;
  required: boolean;
}

interface ChecklistGroup {
  group: string;
  items: ChecklistItem[];
}

const CHECKLIST_GROUPS: ChecklistGroup[] = [
  {
    group: "品牌信息",
    items: [
      { key: "brand.logo", label: "品牌 Logo 已上传", required: true },
      { key: "brand.name", label: "品牌名称已确认", required: true },
      { key: "brand.intro", label: "品牌简介已填写", required: false },
    ],
  },
  {
    group: "产品资料",
    items: [
      { key: "product.created", label: "至少一个产品已创建", required: true },
      { key: "product.sku", label: "SKU 信息已完善", required: true },
      { key: "product.images", label: "产品图片已上传", required: false },
      { key: "product.reports", label: "检测报告已上传", required: true },
      { key: "product.certificates", label: "资质证书已上传", required: true },
      {
        key: "product.batch",
        label: "生产批次信息已录入",
        required: true,
      },
    ],
  },
  {
    group: "码配置",
    items: [
      { key: "code.batch", label: "码批次已创建", required: true },
      { key: "code.generated", label: "码已生成", required: true },
      { key: "code.bound", label: "码已绑定产品/SKU", required: true },
      { key: "code.test", label: "测试码已验证扫码流程", required: true },
    ],
  },
  {
    group: "页面配置",
    items: [
      { key: "page.template", label: "扫码页模板已选择", required: true },
      { key: "page.content", label: "页面内容已配置", required: true },
      { key: "page.preview", label: "页面已预览确认", required: true },
    ],
  },
  {
    group: "活动配置",
    items: [
      { key: "campaign.created", label: "活动已创建（如有）", required: false },
      {
        key: "campaign.benefit",
        label: "权益已配置并测试（如有）",
        required: false,
      },
    ],
  },
  {
    group: "合规配置",
    items: [
      {
        key: "compliance.privacy",
        label: "隐私政策已配置",
        required: true,
      },
      {
        key: "compliance.authorization",
        label: "授权设置已确认",
        required: true,
      },
      {
        key: "compliance.retention",
        label: "数据保留策略已设置",
        required: false,
      },
    ],
  },
];

const STORAGE_KEY = "yimatong_launch_checklist";

function loadCheckedState(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function saveCheckedState(state: Record<string, boolean>) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

export default function LaunchChecklistPage() {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setChecked(loadCheckedState());
    setMounted(true);
  }, []);

  const handleCheck = (key: string, value: boolean) => {
    const next = { ...checked, [key]: value };
    setChecked(next);
    saveCheckedState(next);
  };

  if (!mounted) return null;

  const allItems = CHECKLIST_GROUPS.flatMap((g) => g.items);
  const totalItems = allItems.length;
  const checkedCount = allItems.filter((item) => checked[item.key]).length;
  const requiredItems = allItems.filter((item) => item.required);
  const requiredChecked = requiredItems.filter(
    (item) => checked[item.key]
  ).length;
  const allRequiredDone = requiredChecked === requiredItems.length;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">
          上线检查清单
        </Title>
        <Tag color={allRequiredDone ? "green" : "orange"}>
          {allRequiredDone ? "必填项已完成" : "必填项未完成"}
        </Tag>
      </div>

      <Card className="mb-6">
        <div className="mb-2 flex justify-between text-sm">
          <span>总体进度</span>
          <span>
            {checkedCount} / {totalItems} 项已完成
          </span>
        </div>
        <Progress
          percent={Math.round((checkedCount / totalItems) * 100)}
          status={allRequiredDone ? "success" : "active"}
        />
        <div className="mt-2 text-sm text-text-muted">
          必填项：{requiredChecked} / {requiredItems.length}
        </div>
      </Card>

      {CHECKLIST_GROUPS.map((group) => {
        const groupChecked = group.items.filter(
          (item) => checked[item.key]
        ).length;
        const groupTotal = group.items.length;

        return (
          <Card
            key={group.group}
            title={
              <div className="flex items-center justify-between">
                <span>{group.group}</span>
                <span className="text-sm text-text-muted font-normal">
                  {groupChecked} / {groupTotal}
                </span>
              </div>
            }
            size="small"
            className="mb-4"
          >
            <List
              dataSource={group.items}
              renderItem={(item) => (
                <List.Item style={{ padding: "8px 0" }}>
                  <Checkbox
                    checked={!!checked[item.key]}
                    onChange={(e) => handleCheck(item.key, e.target.checked)}
                  >
                    <span>{item.label}</span>
                    {item.required && (
                      <Tag color="red" className="ml-2" style={{ fontSize: 11 }}>
                        必填
                      </Tag>
                    )}
                  </Checkbox>
                </List.Item>
              )}
            />
          </Card>
        );
      })}
    </div>
  );
}
