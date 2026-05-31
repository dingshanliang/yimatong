"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { CopyOutlined, HolderOutlined, MoreOutlined, SettingOutlined } from "@ant-design/icons";
import { App, Dropdown, Switch, Tag, Tooltip } from "antd";
import { MODULE_TYPES, type ModuleConfig, type ModuleReadiness } from "@/lib/page-dsl";

const STATUS_TAGS: Record<ModuleReadiness["status"], { label: string; color: string }> = {
  configured: { label: "已配置", color: "green" },
  incomplete: { label: "待完善", color: "orange" },
  example: { label: "使用示例数据", color: "blue" },
};

export function ModuleItem({
  module,
  selected,
  readiness,
  onSelect,
  onUpdate,
  onRemove,
  onDuplicate,
}: {
  module: ModuleConfig;
  selected: boolean;
  readiness?: ModuleReadiness;
  onSelect: () => void;
  onUpdate: (updates: Partial<ModuleConfig>) => void;
  onRemove: () => void;
  onDuplicate: () => void;
}) {
  const { modal } = App.useApp();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: module.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const typeLabel = MODULE_TYPES.find((type) => type.value === module.type)?.label || module.type;
  const status = readiness ? STATUS_TAGS[readiness.status] : null;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`rounded border transition-colors ${
        selected ? "border-blue-500 bg-blue-50" : "border-gray-200 bg-white"
      } ${module.enabled === false ? "opacity-60" : ""}`}
    >
      <div
        role="button"
        tabIndex={0}
        className="flex w-full cursor-pointer items-center gap-2 p-3 text-left"
        onClick={onSelect}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect();
          }
        }}
      >
        <span
          {...attributes}
          {...listeners}
          className="cursor-grab text-gray-400 hover:text-gray-600 active:cursor-grabbing"
          onClick={(event) => event.stopPropagation()}
        >
          <HolderOutlined />
        </span>
        <Switch
          size="small"
          checked={module.enabled !== false}
          onChange={(checked) => onUpdate({ enabled: checked })}
          onClick={(_, event) => event.stopPropagation()}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{typeLabel}</span>
            {selected ? <SettingOutlined className="text-blue-500" /> : null}
          </div>
          <div className="mt-1 flex items-center gap-2">
            {status ? <Tag color={status.color}>{status.label}</Tag> : null}
            {readiness?.message ? (
              <Tooltip title={readiness.issues.join("；") || readiness.message}>
                <span className="truncate text-xs text-gray-500">{readiness.message}</span>
              </Tooltip>
            ) : null}
          </div>
        </div>
        <Dropdown
          trigger={["click"]}
          menu={{
            items: [
              { key: "duplicate", icon: <CopyOutlined />, label: "复制模块" },
              { type: "divider" },
              { key: "delete", danger: true, label: "删除模块" },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key === "duplicate") {
                onDuplicate();
                return;
              }
              modal.confirm({
                title: "删除此模块？",
                content: "删除后需要重新添加和配置，当前草稿保存后才会生效。",
                okText: "删除",
                okButtonProps: { danger: true },
                cancelText: "取消",
                onOk: onRemove,
              });
            },
          }}
        >
          <span
            className="rounded px-2 py-1 text-gray-500 hover:bg-gray-100"
            onClick={(event) => event.stopPropagation()}
          >
            <MoreOutlined />
          </span>
        </Dropdown>
      </div>
    </div>
  );
}
