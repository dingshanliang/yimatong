"use client";

import { useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { Button } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ModuleConfig } from "@/lib/page-dsl";
import { ModuleItem } from "./ModuleItem";

export function ModuleList({
  modules,
  productId,
  onChange,
}: {
  modules: ModuleConfig[];
  productId?: string | null;
  onChange: (modules: ModuleConfig[]) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = modules.findIndex((m) => m.id === active.id);
    const newIndex = modules.findIndex((m) => m.id === over.id);
    onChange(arrayMove(modules, oldIndex, newIndex));
  };

  const addModule = () => {
    const id = `mod_${Date.now()}`;
    const newMod: ModuleConfig = { id, type: "product_hero", enabled: true, config: {} };
    onChange([newMod, ...modules]);
    setExpandedId(id);
  };

  const updateModule = (id: string, updates: Partial<ModuleConfig>) => {
    onChange(modules.map((m) => (m.id === id ? { ...m, ...updates } : m)));
  };

  const removeModule = (id: string) => {
    onChange(modules.filter((m) => m.id !== id));
    if (expandedId === id) setExpandedId(null);
  };

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="small" icon={<PlusOutlined />} onClick={addModule}>
          添加模块
        </Button>
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={modules.map((m) => m.id)}
          strategy={verticalListSortingStrategy}
        >
          <div className="space-y-2">
            {modules.map((mod) => (
              <ModuleItem
                key={mod.id}
                module={mod}
                productId={productId}
                expanded={expandedId === mod.id}
                onToggleExpand={() =>
                  setExpandedId(expandedId === mod.id ? null : mod.id)
                }
                onUpdate={(updates) => updateModule(mod.id, updates)}
                onRemove={() => removeModule(mod.id)}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {modules.length === 0 && (
        <div className="py-8 text-center text-gray-400">
          暂无模块，点击&ldquo;添加模块&rdquo;开始
        </div>
      )}
    </div>
  );
}
