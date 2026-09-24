"""PhageScope download action branches (download, save_all, bulk_download)."""

import csv
import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


async def _action_bulk_download(
    *,
    base_url: str,
    timeout: float,
    session_id: Optional[str],
    task_id: Optional[int],
    ancestor_chain: Optional[List[int]],
    save_path: Optional[str],
    pagesize: Optional[int],
    phage_ids: Optional[Any],
    phageids: Optional[str],
    phageid: Optional[str],
    modulelist: Optional[Any],
) -> Dict[str, Any]:
    from .phagescope_bulk_download import phagescope_bulk_download

    # Parse datasources / data_types from various input shapes
    def _coerce_list(val: Any) -> Optional[List[str]]:
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            return [str(v).strip() for v in val if str(v).strip()]
        if isinstance(val, str):
            text = val.strip()
            if not text or text.lower() == "all":
                return None
            return [s.strip() for s in re.split(r"[;,\s]+", text) if s.strip()]
        return None

    return await phagescope_bulk_download(
        datasources=_coerce_list(phage_ids or phageids or phageid),
        data_types=_coerce_list(modulelist),
        base_url=base_url,
        proxy=None,  # resolved from env vars inside bulk_download
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
        save_path=save_path,
        concurrency=int(pagesize) if pagesize and int(pagesize) > 0 else 4,
        timeout=timeout,
    )


async def _action_download(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    taskid: Optional[str],
    download_path: Optional[str],
    save_path: Optional[str],
    preview_bytes: int,
    session_id: Optional[str],
    task_id: Optional[int],
    ancestor_chain: Optional[List[int]],
) -> Dict[str, Any]:
    from . import phagescope as facade

    if not download_path:
        return {"success": False, "status_code": 400, "error": "download_path is required", "action": action}
    path = download_path if download_path.startswith("/") else f"/{download_path}"
    url = f"{base_url}{path}"

    # Bug #7 Fix: Track dynamic path reconstruction status
    dynamic_rebuild_attempted = False
    dynamic_rebuild_success = False

    verify = facade._ssl_verify_enabled(base_url)
    try:
        response = await facade._do_httpx_request(
            "GET",
            url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            verify=verify,
        )
    except httpx.HTTPError as exc:
        if verify and facade._should_retry_without_ssl_verify(base_url, exc):
            facade.logger.warning("PhageScope TLS verification failed for %s; retrying download with verify=False", url)
            response = await facade._do_httpx_request(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
                verify=False,
            )
        else:
            raise
    content_type = response.headers.get("content-type", "")
    content = response.content or b""

    # Fallback: some documented paths (e.g. output/result/phage.tsv) are
    # not directly downloadable from the API root. If taskid is provided,
    # rebuild TSV via structured result endpoint.
    fallback_kind = facade.DOWNLOAD_TSV_FALLBACKS.get(path.lower())
    inferred_kind = facade._infer_result_kind_from_path(path, fallback_kind=fallback_kind)

    # Bug #7 Fix: Try dynamic path reconstruction from task detail uploadpath first
    if response.status_code >= 400 and taskid and not dynamic_rebuild_success:
        dynamic_rebuild_attempted = True
        # First, try to get task detail to extract uploadpath
        try:
            td_status, td_payload = await facade._request(
                "GET",
                base_url,
                "/tasks/detail/",
                params={"taskid": taskid},
                headers=headers,
                timeout=timeout,
            )

            if td_status < 400 and isinstance(td_payload, dict):
                results = td_payload.get("results")
                if isinstance(results, dict):
                    # Try to extract uploadpath from task detail
                    uploadpath = results.get("uploadpath")

                    if uploadpath and isinstance(uploadpath, str):
                        facade.logger.info(f"Download action: extracted uploadpath from task detail: {uploadpath}")

                        # Reconstruct download path from uploadpath
                        # uploadpath is typically like "/workspace/user_task/xxx/output/result/"
                        # We need to append the expected filename
                        filename = facade.RESULT_KIND_TO_FILENAME.get(inferred_kind or "")

                        if filename:
                            # Reconstruct path: uploadpath + filename
                            dynamic_path = uploadpath.rstrip("/") + "/" + filename
                            facade.logger.info(f"Download action: reconstructed dynamic path: {dynamic_path}")

                            # Try downloading with the reconstructed path
                            dynamic_url = f"{base_url}{dynamic_path}"
                            try:
                                dynamic_response = await facade._do_httpx_request(
                                    "GET",
                                    dynamic_url,
                                    headers=headers,
                                    timeout=timeout,
                                    follow_redirects=True,
                                    verify=verify,
                                )
                            except httpx.HTTPError as exc:
                                if verify and facade._should_retry_without_ssl_verify(base_url, exc):
                                    facade.logger.warning(
                                        "PhageScope TLS verification failed for %s; retrying dynamic download with verify=False",
                                        dynamic_url,
                                    )
                                    dynamic_response = await facade._do_httpx_request(
                                        "GET",
                                        dynamic_url,
                                        headers=headers,
                                        timeout=timeout,
                                        follow_redirects=True,
                                        verify=False,
                                    )
                                else:
                                    raise

                            if dynamic_response.status_code < 400:
                                facade.logger.info(f"Download action: dynamic path reconstruction succeeded")
                                response = dynamic_response
                                content_type = dynamic_response.headers.get("content-type", "")
                                content = dynamic_response.content or b""
                                dynamic_rebuild_success = True
                            else:
                                facade.logger.warning(
                                    f"Download action: dynamic path '{dynamic_path}' failed with status {dynamic_response.status_code}"
                                )
                        else:
                            facade.logger.debug(
                                "Download action: could not infer result filename for path '%s' (kind=%s)",
                                path,
                                inferred_kind,
                            )
                    else:
                        facade.logger.debug(
                            f"Download action: no uploadpath found in task detail for taskid={taskid}"
                        )
        except Exception as e:
            facade.logger.warning(f"Download action: failed to fetch task detail for path reconstruction: {e}")
            # Continue to fallback mechanism

    # Fallback to hardcoded path mapping if dynamic reconstruction failed or wasn't attempted
    if response.status_code >= 400 and not dynamic_rebuild_success and inferred_kind and taskid:
        fallback_endpoint = facade.RESULT_ENDPOINTS.get(inferred_kind)
        if not fallback_endpoint:
            fallback_endpoint = facade.RESULT_ENDPOINTS.get(fallback_kind or "")

        if fallback_endpoint:
            facade.logger.info(
                "Download action: using fallback mapping for path '%s' -> result_kind '%s'",
                path,
                inferred_kind,
            )
            fb_status, fb_payload = await facade._request(
                "GET",
                base_url,
                fallback_endpoint,
                params={"taskid": taskid},
                headers=headers,
                timeout=timeout,
            )
            if fb_status < 400 and isinstance(fb_payload, dict):
                tsv_text = facade._results_payload_to_tsv_text(fb_payload)
                if tsv_text is not None:
                    content = tsv_text.encode("utf-8")
                    content_type = "text/tab-separated-values; charset=utf-8"
                    if save_path:
                        dest = Path(save_path).expanduser().resolve()
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(content)
                        return facade._with_api_only_artifact_hint(
                            facade._attach_local_file_artifact_fields(
                                {
                                "success": True,
                                "status_code": 200,
                                "action": action,
                                "content_type": content_type,
                                "content_length": len(content),
                                "fallback": "result_api_tsv",
                                "taskid": str(taskid),
                                "dynamic_rebuild_attempted": dynamic_rebuild_attempted,
                                },
                                local_path=dest,
                                session_id=session_id,
                                task_id=task_id,
                                ancestor_chain=ancestor_chain,
                                output_base_dir=dest.parent,
                            ),
                            str(taskid),
                        )
                    preview = content[: max(preview_bytes, 0)]
                    return facade._with_api_only_artifact_hint(
                        {
                            "success": True,
                            "status_code": 200,
                            "action": action,
                            "data": preview.decode("utf-8", errors="replace"),
                            "content_type": content_type,
                            "content_length": len(content),
                            "preview_bytes": len(preview),
                            "fallback": "result_api_tsv",
                            "taskid": str(taskid),
                            "dynamic_rebuild_attempted": dynamic_rebuild_attempted,
                        },
                        str(taskid),
                    )
        else:
            facade.logger.warning(
                "Download action: no fallback endpoint available for path '%s' (kind=%s)",
                path,
                inferred_kind,
            )

    if save_path:
        if response.status_code >= 400:
            preview = content[: max(preview_bytes, 0)].decode("utf-8", errors="replace")
            return {
                "success": False,
                "status_code": response.status_code,
                "action": action,
                "error": f"Download failed: HTTP {response.status_code}",
                "content_type": content_type,
                "content_length": len(content),
                "preview": preview,
            }
        dest = Path(save_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        dl_ok = facade._attach_local_file_artifact_fields(
            {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "action": action,
            "content_type": content_type,
            "content_length": len(content),
            },
            local_path=dest,
            session_id=session_id,
            task_id=task_id,
            ancestor_chain=ancestor_chain,
            output_base_dir=dest.parent,
        )
        if taskid:
            dl_ok["taskid"] = str(taskid)
        return facade._with_api_only_artifact_hint(dl_ok, str(taskid) if taskid else None)
    preview = content[: max(preview_bytes, 0)]
    if "application/json" in content_type:
        try:
            payload = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {"raw": content.decode("utf-8", errors="replace")}
        return facade._with_api_only_artifact_hint(
            facade._response_with_business_layer(
                action,
                response.status_code,
                payload,
                content_type=content_type,
                content_length=len(content),
            ),
            str(taskid) if taskid else None,
        )
    if content_type.startswith("text/"):
        return facade._with_api_only_artifact_hint(
            {
                "success": response.status_code < 400,
                "status_code": response.status_code,
                "action": action,
                "data": preview.decode("utf-8", errors="replace"),
                "content_type": content_type,
                "content_length": len(content),
                "preview_bytes": len(preview),
            },
            str(taskid) if taskid else None,
        )
    return facade._with_api_only_artifact_hint(
        {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "action": action,
            "content_type": content_type,
            "content_length": len(content),
            "preview_bytes": len(preview),
        },
        str(taskid) if taskid else None,
    )


async def _action_save_all(
    *,
    action: str,
    base_url: str,
    headers: Dict[str, str],
    timeout: float,
    taskid: Optional[str],
    save_path: Optional[str],
    session_id: Optional[str],
    task_id: Optional[int],
    ancestor_chain: Optional[List[int]],
) -> Dict[str, Any]:
    from . import phagescope as facade

    # Requires taskid; optionally accepts output_dir
    if not taskid:
        return {"success": False, "status_code": 400, "error": "taskid is required", "action": action}

    # Determine output directory
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    session_root = facade._resolve_session_phagescope_root(session_id)
    if session_id and task_id is not None and not save_path:
        from app.services.path_router import get_path_router

        router = get_path_router()
        default_output_dir = router.get_task_output_dir(
            session_id,
            task_id,
            ancestor_chain,
            create=True,
        )
    elif session_root is not None:
        default_output_dir = session_root / f"task_{taskid}_{timestamp_str}"
    else:
        resolver = facade.get_tool_output_resolver()
        default_output_dir = resolver.resolve(session_id=None, tool_name="phagescope", create=True) / f"task_{taskid}_{timestamp_str}"
    output_dir = Path(save_path) if save_path else default_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    metadata_dir = output_dir / "metadata"
    annotation_dir = output_dir / "annotation"
    sequences_dir = output_dir / "sequences"
    phylogeny_dir = output_dir / "phylogeny"
    raw_dir = output_dir / "raw_api_responses"

    for d in [metadata_dir, annotation_dir, sequences_dir, phylogeny_dir, raw_dir]:
        d.mkdir(parents=True, exist_ok=True)

    saved_files: Dict[str, str] = {}
    raw_responses: Dict[str, Any] = {}
    errors: List[str] = []

    # Helper to fetch and save a result kind
    async def fetch_and_save(result_kind: str) -> Optional[Dict[str, Any]]:
        endpoint = facade.RESULT_ENDPOINTS.get(result_kind)
        if not endpoint:
            return None
        try:
            status_code, payload = await facade._request(
                "GET", base_url, endpoint, params={"taskid": taskid}, headers=headers, timeout=timeout
            )
            raw_responses[result_kind] = {"status_code": status_code, "payload": payload}

            # Save raw response
            raw_file = raw_dir / f"{result_kind}_raw.json"
            raw_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

            if status_code >= 400:
                errors.append(f"{result_kind}: HTTP {status_code}")
                return None
            rk_ok, rk_meta = facade._merge_http_and_business_success(status_code, payload)
            if not rk_ok:
                err = rk_meta.get("error") or ""
                suffix = f" ({err})" if err else ""
                errors.append(
                    f"{result_kind}: business code {rk_meta.get('business_code')}{suffix}"
                )
                return None
            return payload
        except Exception as e:
            errors.append(f"{result_kind}: {str(e)}")
            return None

    # 1. Fetch task detail first for metadata
    detail_status, detail_payload = await facade._request(
        "GET", base_url, "/tasks/detail/", params={"taskid": taskid}, headers=headers, timeout=timeout
    )
    if detail_status < 400:
        td_ok, td_meta = facade._merge_http_and_business_success(detail_status, detail_payload)
        if not td_ok:
            err = td_meta.get("error") or ""
            suffix = f" ({err})" if err else ""
            errors.append(
                f"task_detail: business code {td_meta.get('business_code')}{suffix}"
            )
    raw_responses["task_detail"] = {"status_code": detail_status, "payload": detail_payload}
    (raw_dir / "task_detail_raw.json").write_text(
        json.dumps(detail_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 2. Fetch phage info
    phage_data = await fetch_and_save("phage")
    if phage_data:
        phage_file = metadata_dir / "phage_info.json"
        phage_file.write_text(json.dumps(phage_data, indent=2, ensure_ascii=False), encoding="utf-8")
        saved_files["phage_info"] = str(phage_file.relative_to(output_dir))

    # 3. Fetch quality
    quality_data = await fetch_and_save("quality")
    if quality_data:
        quality_file = metadata_dir / "quality.json"
        quality_file.write_text(json.dumps(quality_data, indent=2, ensure_ascii=False), encoding="utf-8")
        saved_files["quality"] = str(quality_file.relative_to(output_dir))

    # 4. Fetch proteins and save as both JSON and TSV
    proteins_data = await fetch_and_save("proteins")
    if proteins_data:
        proteins_json_file = annotation_dir / "proteins.json"
        proteins_json_file.write_text(json.dumps(proteins_data, indent=2, ensure_ascii=False), encoding="utf-8")
        saved_files["proteins_json"] = str(proteins_json_file.relative_to(output_dir))

        # Convert to TSV if results is a list
        results_list = proteins_data.get("results") if isinstance(proteins_data, dict) else None
        if isinstance(results_list, list) and results_list:
            proteins_tsv_file = annotation_dir / "proteins.tsv"
            # Get all unique keys from all records
            all_keys: List[str] = []
            for record in results_list:
                if isinstance(record, dict):
                    for key in record.keys():
                        if key not in all_keys:
                            all_keys.append(key)
            if all_keys:
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=all_keys, delimiter="\t", extrasaction="ignore")
                writer.writeheader()
                for record in results_list:
                    if isinstance(record, dict):
                        writer.writerow(record)
                proteins_tsv_file.write_text(output.getvalue(), encoding="utf-8")
                saved_files["proteins_tsv"] = str(proteins_tsv_file.relative_to(output_dir))

    # 5. Fetch phagefasta (FASTA sequences)
    fasta_data = await fetch_and_save("phagefasta")
    if fasta_data:
        fasta_content = None
        # Try to extract actual FASTA content
        if isinstance(fasta_data, dict):
            fasta_content = fasta_data.get("results") or fasta_data.get("fasta") or fasta_data.get("data")
        if isinstance(fasta_content, str) and fasta_content.strip():
            fasta_file = sequences_dir / "phage.fasta"
            fasta_file.write_text(fasta_content, encoding="utf-8")
            saved_files["fasta"] = str(fasta_file.relative_to(output_dir))
        else:
            # Save as JSON if not plain text
            fasta_json_file = sequences_dir / "phagefasta.json"
            fasta_json_file.write_text(json.dumps(fasta_data, indent=2, ensure_ascii=False), encoding="utf-8")
            saved_files["fasta_json"] = str(fasta_json_file.relative_to(output_dir))

    # 6. Fetch tree (phylogenetic tree)
    tree_data = await fetch_and_save("tree")
    if tree_data:
        tree_content = None
        if isinstance(tree_data, dict):
            tree_content = tree_data.get("results") or tree_data.get("tree") or tree_data.get("newick")
        # Check if it looks like Newick format
        if isinstance(tree_content, str) and ("(" in tree_content and ")" in tree_content):
            tree_file = phylogeny_dir / "tree.nwk"
            tree_file.write_text(tree_content, encoding="utf-8")
            saved_files["tree_newick"] = str(tree_file.relative_to(output_dir))
        else:
            # Save as JSON
            tree_json_file = phylogeny_dir / "tree.json"
            tree_json_file.write_text(json.dumps(tree_data, indent=2, ensure_ascii=False), encoding="utf-8")
            saved_files["tree_json"] = str(tree_json_file.relative_to(output_dir))

    # 7. Fetch modules info
    module_names: List[str] = []
    if isinstance(detail_payload, dict):
        detail_results = detail_payload.get("results")
        if isinstance(detail_results, dict):
            module_names = facade._parse_modulelist(detail_results.get("modulelist"))

    if module_names:
        modules_payload: Dict[str, Any] = {}
        for module_name in module_names:
            module_name = str(module_name).strip()
            if not module_name:
                continue
            safe_module_name = "".join(
                ch if (ch.isalnum() or ch in {"_", "-"}) else "_"
                for ch in module_name
            )
            try:
                status_code, payload = await facade._request(
                    "GET",
                    base_url,
                    facade.RESULT_ENDPOINTS["modules"],
                    params={"taskid": taskid, "module": module_name},
                    headers=headers,
                    timeout=timeout,
                )
                raw_key = f"modules:{module_name}"
                raw_responses[raw_key] = {"status_code": status_code, "payload": payload}
                raw_file = raw_dir / f"modules_{safe_module_name}_raw.json"
                raw_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                if status_code >= 400:
                    errors.append(f"modules[{module_name}]: HTTP {status_code}")
                    continue
                mod_ok, mod_meta = facade._merge_http_and_business_success(status_code, payload)
                if not mod_ok:
                    err = mod_meta.get("error") or ""
                    suffix = f" ({err})" if err else ""
                    errors.append(
                        f"modules[{module_name}]: business code {mod_meta.get('business_code')}{suffix}"
                    )
                    continue

                # Persist per-module payload for easier downstream debugging/consumption.
                module_out_file = annotation_dir / f"module_{safe_module_name}.json"
                module_out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                saved_files[f"module_{safe_module_name}"] = str(module_out_file.relative_to(output_dir))
                modules_payload[module_name] = payload

                # Fallbacks for flaky result endpoints:
                # - quality endpoint may 500 while modules[quality] is available
                # - proteins endpoint may 500 while modules[annotation] holds annotation records
                if module_name.lower() == "quality" and "quality" not in saved_files:
                    quality_fallback_file = metadata_dir / "quality_from_modules.json"
                    quality_fallback_file.write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    saved_files["quality"] = str(quality_fallback_file.relative_to(output_dir))

                if module_name.lower() == "annotation" and "proteins_json" not in saved_files:
                    proteins_fallback_file = annotation_dir / "proteins_from_annotation.json"
                    proteins_fallback_file.write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    saved_files["proteins_json"] = str(proteins_fallback_file.relative_to(output_dir))

                    # Try deriving TSV when annotation payload has tabular-like records.
                    ann_results = payload.get("results") if isinstance(payload, dict) else None
                    if isinstance(ann_results, list) and ann_results:
                        all_keys: List[str] = []
                        for record in ann_results:
                            if isinstance(record, dict):
                                for key in record.keys():
                                    if key not in all_keys:
                                        all_keys.append(key)
                        if all_keys:
                            out = StringIO()
                            writer = csv.DictWriter(out, fieldnames=all_keys, delimiter="\t", extrasaction="ignore")
                            writer.writeheader()
                            for record in ann_results:
                                if isinstance(record, dict):
                                    writer.writerow(record)
                            proteins_tsv_fallback = annotation_dir / "proteins_from_annotation.tsv"
                            proteins_tsv_fallback.write_text(out.getvalue(), encoding="utf-8")
                            saved_files["proteins_tsv"] = str(proteins_tsv_fallback.relative_to(output_dir))
            except Exception as e:
                errors.append(f"modules[{module_name}]: {str(e)}")
        if modules_payload:
            modules_file = metadata_dir / "modules.json"
            modules_file.write_text(json.dumps(modules_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            saved_files["modules"] = str(modules_file.relative_to(output_dir))
    else:
        # Backward compatibility for servers that may support modules aggregation.
        modules_data = await fetch_and_save("modules")
        if modules_data:
            modules_file = metadata_dir / "modules.json"
            modules_file.write_text(json.dumps(modules_data, indent=2, ensure_ascii=False), encoding="utf-8")
            saved_files["modules"] = str(modules_file.relative_to(output_dir))

    # 8. Create summary.json
    summary = {
        "taskid": taskid,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "output_directory": str(output_dir.resolve()),
        "files": saved_files,
        "errors": errors if errors else None,
        "task_detail": detail_payload.get("results") if isinstance(detail_payload, dict) else None,
    }
    summary_file = output_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Decide success semantics:
    # - If any endpoint failed, we return 207 (Multi-Status).
    # - But if core artifacts are present, treat it as usable success with warnings.
    has_quality = "quality" in saved_files
    has_proteins = ("proteins_tsv" in saved_files) or ("proteins_json" in saved_files)
    has_phage_info = "phage_info" in saved_files
    requested_module_set = {
        str(module_name).strip().lower()
        for module_name in (module_names or [])
        if str(module_name).strip()
    }
    expects_quality = ("quality" in requested_module_set) or (not requested_module_set)
    expects_proteins = ("annotation" in requested_module_set) or (not requested_module_set)
    core_saved = ((not expects_quality) or has_quality) and ((not expects_proteins) or has_proteins)

    missing_artifacts: List[str] = []
    # Derive missing artifacts from errors (e.g. "phagefasta: HTTP 500")
    if errors:
        for item in errors:
            if not isinstance(item, str):
                continue
            name = item.split(":", 1)[0].strip()
            if name and name not in missing_artifacts:
                missing_artifacts.append(name)

    # Also infer missing of core files if absent
    if expects_quality and (not has_quality) and "quality" not in missing_artifacts:
        missing_artifacts.append("quality")
    if expects_proteins and (not has_proteins) and "proteins" not in missing_artifacts:
        missing_artifacts.append("proteins")
    if not has_phage_info and "phage" not in missing_artifacts:
        missing_artifacts.append("phage")

    # If fallback files were successfully generated, remove stale core-missing markers.
    if has_quality:
        missing_artifacts = [m for m in missing_artifacts if m != "quality"]
    if has_proteins:
        missing_artifacts = [m for m in missing_artifacts if m != "proteins"]
    if has_phage_info:
        missing_artifacts = [m for m in missing_artifacts if m != "phage"]
    if not expects_quality:
        missing_artifacts = [m for m in missing_artifacts if m != "quality"]
    if not expects_proteins:
        missing_artifacts = [m for m in missing_artifacts if m != "proteins"]

    partial = len(errors) > 0
    warnings: List[str] = []
    if partial and missing_artifacts:
        warnings.append(
            "Partial download: some result kinds failed. Core results are available; missing: "
            + ", ".join(missing_artifacts[:6])
            + ("..." if len(missing_artifacts) > 6 else "")
        )

    return facade._attach_local_bundle_artifact_fields(
        {
        "success": True if (core_saved or len(errors) == 0) else False,
        "status_code": 200 if len(errors) == 0 else 207,  # 207 = Multi-Status
        "action": action,
        "artifact_scope": "local_bundle",
        "taskid": taskid,
        "output_directory": str(output_dir.resolve()),
        "output_directory_rel": str(output_dir),
        "files_saved": saved_files,
        "errors": errors if errors else None,
        "partial": True if partial else False,
        "missing_artifacts": missing_artifacts if missing_artifacts else None,
        "warnings": warnings if warnings else None,
        "summary_file": str(summary_file.resolve()),
        "summary_file_rel": str(summary_file),
        },
        output_dir=output_dir,
        saved_files=saved_files,
        summary_file=summary_file,
        session_id=session_id,
        task_id=task_id,
        ancestor_chain=ancestor_chain,
    )


__all__ = [
    "_action_bulk_download",
    "_action_download",
    "_action_save_all",
]
