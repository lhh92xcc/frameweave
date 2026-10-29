"""Mandatory identity anchors, optionally strengthened by approved Markdown."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from studio.models import REFERENCE_ROLES, StrictModel


class AssetReferences(StrictModel):
    required_roles: list[str] = Field(min_length=1, max_length=8)
    shot_roles: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def valid_roles(self):
        if (len(set(self.required_roles)) != len(self.required_roles)
                or len(set(self.shot_roles)) != len(self.shot_roles)):
            raise ValueError("参考视角不能重复")
        if not set(self.shot_roles) <= set(self.required_roles) or self.required_roles[0] not in self.shot_roles:
            raise ValueError("逐镜参考必须包含母版，且来自必备视角")
        return self


class ReferencePolicy(StrictModel):
    version: Literal[1]
    assets: dict[str, AssetReferences] = Field(max_length=120)
    continuity: Literal["previous_shot"] = "previous_shot"
    max_references: int = Field(default=9, ge=1, le=9)


def read_policy(markdown):
    marker = "```frameweave-reference-policy"
    if marker not in markdown:
        return None
    blocks = re.findall(r"^```frameweave-reference-policy\s*\n(.*?)^```\s*$", markdown, re.M | re.S)
    if len(blocks) != 1 or markdown.count(marker) != 1:
        raise ValueError("强制参考规则必须是唯一、完整的 frameweave-reference-policy 代码块")
    try:
        policy = ReferencePolicy.model_validate_json(blocks[0])
        if not policy.assets:
            raise ValueError("参考规则不能缺少资产")
        return policy
    except ValidationError:
        raise ValueError("强制参考规则格式无效，请检查资产、必备视角及逐镜视角") from None


def validate_plan(policy, plan):
    if policy is None:
        return
    assets = {asset["id"]: asset for asset in plan["assets"]}
    if set(policy.assets) != set(assets):
        raise ValueError("强制参考规则必须覆盖当前分镜的全部资产 ID，不能遗漏或引用不存在的资产")
    for id_, rules in policy.assets.items():
        if not set(rules.required_roles) <= REFERENCE_ROLES[assets[id_]["kind"]]:
            raise ValueError(f"{id_} 的必备参考视角与素材类型不匹配")
        if assets[id_]["kind"] == "character" and (
            rules.required_roles[0] != "front" or len(rules.shot_roles) < 2
        ):
            raise ValueError(f"{id_} 必须以正面母版锁定身份，并绑定至少两个逐镜视角")


class ReferenceGuard:
    def __init__(self, workflow, project):
        self.workflow, self.project = workflow, project
        self.explicit = read_policy(project["contract"]["markdown"])
        self.policy = self.explicit
        if not self.policy and project.get("plan"):
            self.policy = ReferencePolicy(version=1, assets={
                asset["id"]: AssetReferences(required_roles=["general"], shot_roles=["general"])
                for asset in project["plan"]["assets"]
            })

    def canonical(self, entity_id):
        """An approved independent original remains the anchor, never a derivative."""
        candidates = [a for a in self.workflow.store.artifacts(self.project["id"])
                      if a["kind"] == "reference" and a["entity_id"] == entity_id
                      and not a.get("archived") and not a.get("dependencies")
                      and a["contract_hash"] == self.project["contract"]["hash"]
                      and a["plan_revision"] == self.project["plan_revision"]]
        binding = self.project.get("reference_masters", {}).get(entity_id)
        if binding:
            return next((a for a in candidates if a["id"] == binding), None)
        established = [a for a in candidates if a["review"] == "approved" or a.get("approved_once")]
        return min(established, key=lambda a: a["created_at"]) if established else (
            max(candidates, key=lambda a: a["created_at"]) if candidates else None
        )

    def views(self, entity_id):
        """Keep the newest candidate per role, including pending/rejected replacements."""
        result = {}
        for artifact in self.workflow.store.artifacts(self.project["id"]):
            if (
                artifact["kind"] != "reference" or artifact["entity_id"] != entity_id
                or artifact.get("archived") or artifact["contract_hash"] != self.project["contract"]["hash"]
                or artifact["plan_revision"] != self.project["plan_revision"]
            ):
                continue
            role = artifact.get("metadata", {}).get("reference_role", "general")
            if role not in result or artifact["created_at"] > result[role]["created_at"]:
                result[role] = artifact
        if not self.explicit:
            master = self.canonical(entity_id)
            if master:
                result["general"] = master
        return result

    def master(self, entity_id, source_id=None):
        rules = self.policy.assets[entity_id]
        master = (self.views(entity_id).get(rules.required_roles[0]) if self.explicit
                  else self.canonical(entity_id))
        if not master or master["review"] != "approved":
            raise ValueError(f"{entity_id} 缺少已批准的 {rules.required_roles[0]} 母版；请先上传并审核，禁止无参考生图")
        if master.get("dependencies"):
            raise ValueError(f"{entity_id} 母版必须是独立上传并批准的原始定妆图")
        if source_id and master["id"] != source_id:
            raise ValueError("必须复用当前角色母版，不能用派生图或其他视角重新定义身份")
        return master

    def approved(self, artifact, master):
        if artifact["review"] != "approved":
            return False
        if artifact["id"] == master["id"]:
            return True
        return (
            artifact.get("dependencies") == [master["id"]]
            and artifact.get("dependency_versions") == self.workflow.dependency_versions([master["id"]])
        )

    def current(self, artifact):
        """Pending views are reviewable only while their canonical source is current."""
        rules = self.policy.assets.get(artifact["entity_id"])
        role = artifact.get("metadata", {}).get("reference_role", "general")
        if not self.explicit:
            if not rules:
                return False
            master = self.canonical(artifact["entity_id"])
            if not master:
                return False
            if not artifact.get("dependencies"):
                return (artifact["contract_hash"] == self.project["contract"]["hash"]
                        and artifact["plan_revision"] == self.project["plan_revision"])
            return (master["review"] == "approved" and artifact.get("dependencies") == [master["id"]]
                    and artifact.get("dependency_versions") == self.workflow.dependency_versions([master["id"]]))
        if not rules or self.views(artifact["entity_id"]).get(role, {}).get("id") != artifact["id"]:
            return False
        if role == rules.required_roles[0]:
            return not artifact.get("dependencies")
        try:
            master = self.master(artifact["entity_id"])
            return (artifact.get("dependencies") == [master["id"]]
                    and artifact.get("dependency_versions") == self.workflow.dependency_versions([master["id"]]))
        except (ValueError, KeyError):
            return False

    def shot_references(self, entity_id):
        rules = self.policy.assets[entity_id]
        master, views = self.master(entity_id), self.views(entity_id)
        missing = [role for role in rules.required_roles
                   if role not in views or not self.approved(views[role], master)]
        if missing:
            raise ValueError(f"{entity_id} 多视角参考未齐全或未批准：{'、'.join(missing)}；禁止生成剧情画面")
        from studio.archive_import import sha256

        if len(rules.required_roles) > 1:
            checksums = []
            for role in rules.required_roles:
                with self.workflow.cloud.materialize(self.workflow.store, views[role]) as path:
                    checksums.append(sha256(path))
            if len(set(checksums)) != len(checksums):
                raise ValueError(f"{entity_id} 多视角引用了相同图片；不能靠重复上传或改分类补齐视角")
        return [views[role]["id"] for role in rules.shot_roles]

    def target_role(self, entity_id, requested=None):
        if not self.explicit:
            candidate = self.canonical(entity_id)
            if not candidate and self.has_previous_reference(entity_id):
                raise ValueError("已有旧版本资产原图，请重新导入并按当前规范审核，禁止无参考重定身份")
            if candidate and candidate["review"] == "pending":
                raise ValueError("已有待确认母版，请先审核，避免重复生成")
            if self.project.get("reference_masters", {}).get(entity_id) or (candidate and candidate.get("approved_once")):
                self.master(entity_id)
            if requested and requested != "general":
                self.master(entity_id)
            return requested or "general"
        rules = self.policy.assets[entity_id]
        role = requested
        if role is None:
            master, views = self.master(entity_id), self.views(entity_id)
            role = next((r for r in rules.required_roles[1:]
                         if r not in views or not self.approved(views[r], master)), None)
            if role in views and views[role]["review"] == "pending" and self.current(views[role]):
                raise ValueError(f"{entity_id} 的 {role} 视角已有待审结果，请先审核，避免重复生成")
        if role is None:
            raise ValueError("多视角已齐全，请明确选择需要重做的视角")
        if role not in rules.required_roles or role == rules.required_roles[0]:
            raise ValueError("母版不能在多视角生成中重做；请选择必备派生视角，换母版需重新上传并审核")
        return role

    def has_previous_reference(self, entity_id):
        return any(a["kind"] == "reference" and a["entity_id"] == entity_id and not a.get("archived")
                   and (a["contract_hash"] != self.project["contract"]["hash"]
                        or a["plan_revision"] != self.project["plan_revision"])
                   for a in self.workflow.store.artifacts(self.project["id"]))

    def previous_shot(self, shot_id):
        for episode in self.project["plan"]["episodes"]:
            for index, shot in enumerate(episode["shots"]):
                if shot["id"] == shot_id:
                    return episode["shots"][index - 1]["id"] if index else None
        raise ValueError("找不到连续性分镜")

    def attachment_count(self, ids):
        return len({self.attachment_group(self.workflow.store.artifact(id_)) for id_ in ids})

    def attachment_group(self, artifact):
        rules = self.policy.assets.get(artifact["entity_id"])
        if artifact["kind"] == "reference" and rules and rules.required_roles[0] == "front":
            return "character:" + artifact["entity_id"]
        return artifact["id"]

    def status(self):
        if not self.policy:
            return None
        result = {"assets": {}, "max_references": self.policy.max_references, "continuity": self.policy.continuity,
                  "mode": "explicit" if self.explicit else "automatic"}
        for entity_id, rules in self.policy.assets.items():
            views = self.views(entity_id)
            master = views.get(rules.required_roles[0])
            valid_master = master and master["review"] == "approved" and not master.get("dependencies")
            result["assets"][entity_id] = {
                "required_roles": rules.required_roles, "shot_roles": rules.shot_roles,
                "views": [{"role": role, "artifact_id": views.get(role, {}).get("id"),
                           "approved": bool(valid_master and role in views and self.approved(views[role], master))}
                          for role in rules.required_roles],
            }
        return result


def prepare_reference_images(workflow, job, paths):
    """Local contact sheets keep every bound view within attachment limits."""
    from PIL import Image, ImageDraw, ImageOps

    from studio.archive_import import sha256
    from studio.store import digest

    guard = ReferenceGuard(workflow, workflow.store.project(job["project_id"]))
    if not guard.policy or job["stage"] != "keyframe":
        return paths
    groups = {}
    for id_, path in zip(job["snapshot"]["dependencies"], paths, strict=True):
        artifact = workflow.store.artifact(id_)
        groups.setdefault(guard.attachment_group(artifact), []).append((artifact, path))
    outputs, manifest = [], []
    for entries in groups.values():
        source_ids = [artifact["id"] for artifact, _ in entries]
        if len(entries) == 1:
            path = entries[0][1]
        else:
            cols = min(3, len(entries))
            sheet = Image.new("RGB", (960 * cols, 1000 * ((len(entries) + cols - 1) // cols)), "#eeeeee")
            draw = ImageDraw.Draw(sheet)
            for index, (artifact, source) in enumerate(entries):
                with Image.open(source) as image:
                    view = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGB"), (960, 960))
                x, y = (index % cols) * 960, (index // cols) * 1000
                sheet.paste(view, (x + (960 - view.width) // 2, y + 40 + (960 - view.height) // 2))
                draw.text((x + 12, y + 12), artifact.get("metadata", {}).get("reference_role", "general"), fill="black")
            signature = digest([(id_, sha256(path)) for id_, (_, path) in zip(source_ids, entries, strict=True)])
            path = workflow.store.media / ("reference-sheet-" + signature + ".png")
            sheet.save(path, "PNG")
        outputs.append(path)
        manifest.append({"source_artifact_ids": source_ids, "file": path.name, "sha256": sha256(path)})
    if len(outputs) > guard.policy.max_references:
        raise ValueError("实际上传参考卡超出上限，禁止静默丢图")
    job["runtime"]["reference_uploads"] = manifest
    workflow.store.save_job(job)
    return outputs
