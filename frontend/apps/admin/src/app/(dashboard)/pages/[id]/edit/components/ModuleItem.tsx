"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { HolderOutlined } from "@ant-design/icons";
import { Button, Popconfirm, Select, Switch } from "antd";
import { MODULE_TYPES, type ModuleConfig, type ModuleType } from "@/lib/page-dsl";
import { ModuleConfigForm } from "./ModuleConfigForms";

export function ModuleItem({
  module,
  expanded,
  onToggleExpand,
  onUpdate,
  onRemove,
}: {
  module: ModuleConfig;
  expanded: boolean;
  onToggleExpand: () => void;
  onUpdate: (updates: Partial<ModuleConfig>) => void;
  onRemove: () => void;
}) {
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

  const typeLabel =
    MODULE_TYPES.find((t) => t.value === module.type)?.label || module.type;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`rounded border transition-colors ${
        module.enabled ? "bg-white" : "bg-gray-50 opacity-60"
      } ${expanded ? "ring-2 ring-blue-200" : ""}`}
    >
      <div
        className="flex items-center gap-2 p-3 cursor-pointer"
        onClick={onToggleExpand}
      >
        <span
          {...attributes}
          {...listeners}
          className="cursor-grab text-gray-400 hover:text-gray-600 active:cursor-grabbing"
        >
          <HolderOutlined />
        </span>
        <Switch
          size="small"
          checked={module.enabled}
          onChange={(checked) => onUpdate({ enabled: checked })}
          onClick={(_, e) => e.stopPropagation()}
        />
        <span className="text-sm font-medium">{typeLabel}</span>
        <span className="text-xs text-gray-400">{module.id}</span>
        <div className="flex-1" />
        <Popconfirm
          title="删除此模块？"
          onConfirm={onRemove}
          onCancel={(e) => e?.stopPropagation()}
        >
          <Button
            size="small"
            danger
            type="text"
            onClick={(e) => e.stopPropagation()}
          >
            删除
          </Button>
        </Popconfirm>
      </div>

      {expanded && (
        <div className="border-t px-3 pb-3 pt-2">
          <div className="mb-2 flex gap-2">
            <Select
              size="small"
              value={module.type}
              onChange={(type: ModuleType) => onUpdate({ type })}
              options={MODULE_TYPES}
              style={{ width: 140 }}
            />
          </div>
          <div className="rounded bg-gray-50 p-2">
            <ModuleConfigForm
              moduleType={module.type}
              config={module.config || {}}
              onChange={(config) => onUpdate({ config })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
