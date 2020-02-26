#!/bin/env python3

"""
file_tree = []

rm -rf target_dir/*

walk target_dir
    if file is file
        add to tree


target_dir:
    ...
    *.md
    dir/
        *.md
"""

from __future__ import annotations  # PEP-563

import pathlib
from dataclasses import dataclass, field
from argparse import Namespace
import argparse
from os import listdir
from os.path import isdir, join as path_join
from sys import argv, exit
from typing import cast, List, Optional
import re

from result import Ok, Err, Result
import markdown
import json


errstr = str

EXTENSIONS = [
    "fenced_code",
    "toc",
]


indent = 0


@dataclass
class Config:
    posts_dir: Optional[str] = None
    include_before: Optional[str] = None
    include_after: Optional[str] = None
    static_src: Optional[str] = None
    static_dest: Optional[str] = None


@dataclass
class FileMetadata:
    title: str = ""
    desc: str = ""


@dataclass
class File:
    path: str
    metadata: FileMetadata = field(default_factory=FileMetadata)
    is_post: bool = False
    md: str = ""
    html: str = ""

    def __repr__(self):
        return (
            "File(\n"
            f"\tpath={self.path}, \n"
            f"\tmetadata={self.metadata}, \n"
            f"\tis_post={self.is_post}, \n"
            f'\tmd="{self.md[:min(len(self.md), 10)]}...", \n'
            f'\thtml="{self.html[:min(len(self.html), 10)]}...", \n'
            ")"
        )


@dataclass
class FileTree:
    path: str
    files: List[File] = field(default_factory=list)
    trees: List[FileTree] = field(default_factory=list)

    def __repr__(self):
        s = ""
        for f in self.files:
            s += repr(f) + "\n"
        for t in self.trees:
            s += repr(t) + "\n"
        return s[:-1]


@dataclass
class State:
    posts: List[File] = field(default_factory=list)
    static_pages: List[File] = field(default_factory=list)
    include_before: str = ""
    include_after: str = ""


def main(argv: List[str]) -> int:
    # Parse args
    args = parse_args(argv)

    # Load config
    r_config = read_config(args.srcdir)
    if r_config.is_err():
        print(r_config.err())
        return 1
    config = cast(Config, r_config.ok())

    state = State()

    load_before_after_files(args.srcdir, config, state)

    # Build file tree
    r_tree = build_file_tree(args.srcdir, args.srcdir, config)
    if r_tree is None:
        print(r_tree.err())
        return 1
    tree = cast(FileTree, r_tree.ok())

    process_pages(state, tree)

    preprocess_pages(state, tree)

    # compile files
    compile_files(state)

    #  for f in (state.posts + state.static_pages):
    #  print(f"=========== {f.path} ===========")
    #  print(f.md)
    #  print()
    #  print()

    # clean target directory

    # write files
    write_files(state, args.srcdir, args.outdir)

    import shutil

    shutil.copytree(
        path_join(args.srcdir, config.static_src),
        path_join(args.outdir, config.static_dest),
    )

    return 0


def write_files(state: State, srcdir: str, outdir: str):
    for p in state.static_pages + state.posts:
        path = p.path.replace(srcdir, outdir + "/")
        write_file(path, p.html)


def write_file(path: str, data: str) -> None:
    pathlib.Path(path[: path.rfind("/")]).mkdir(parents=True, exist_ok=True)
    path = path.replace(".md", ".html")
    with open(path, "w") as f:
        f.write(data)


def compile_files(state: State):
    combined: List[File] = state.static_pages + state.posts
    for file in combined:
        file.html = (
            state.include_before + render_md(file.md).unwrap() + state.include_after
        )


def load_md_file(
    state: State, metadata: FileMetadata, path: str
) -> Result[errstr, str]:
    try:
        with open(path) as f:
            return Ok(f.read())
    except IOError as e:
        Err(str(e))
    except FileNotFoundError as e:
        Err(str(e))

    return Err(f"Failed to compile file {path}")


def process_pages(state: State, tree: FileTree):
    for file in tree.files:
        if file.is_post:
            state.posts.append(file)
        else:
            state.static_pages.append(file)
        file.md = load_md_file(state, file.metadata, file.path).unwrap()

    for subtree in tree.trees:
        process_pages(state, subtree)


def preprocess_pages(state: State, tree: FileTree) -> None:
    # Process `posts` first then `static_pages`
    for p in state.posts + state.static_pages:
        p.md = preprocess_md(state, p.metadata, p.md).unwrap()


def read_config(root: str) -> Result[errstr, Config]:
    path = path_join(root, "config.json")
    try:
        with open(path) as f:
            j = json.loads(f.read())
            c = Config()

            props = [
                "posts dir",
                "include before",
                "include after",
                "static src",
                "static dest",
            ]
            for prop in props:
                try:
                    v = j[prop]
                    setattr(c, prop.replace(" ", "_"), v)
                except KeyError as e:
                    continue

            return Ok(c)
    except TypeError as e:
        return Err(f"Failed to read config: {e}")
    except FileNotFoundError as e:
        return Err(f"Couldn't find {path}. Does it exist?")


def parse_args(argv: List[str]) -> Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("srcdir")
    parser.add_argument("outdir")
    return parser.parse_args()


def rm_directory_contents(path):
    ...


def load_before_after_files(root: str, config: Config, state: State):
    try:
        if config.include_before is not None:
            with open(path_join(root, config.include_before), "r") as f:
                state.include_before = f.read()
        if config.include_after is not None:
            with open(path_join(root, config.include_after), "r") as f:
                state.include_after = f.read()
    except Exception as e:
        pass


def build_file_tree(
    dir: str, root: str, config: Config, tree: Optional[FileTree] = None
) -> Result[errstr, FileTree]:
    if tree is None:
        tree = FileTree(path=dir)

    excluded = [
        "config.json",
        config.include_before,
        config.include_after,
        config.static_src,
    ]

    try:
        dir_files = listdir(dir)
    except NotADirectoryError as e:
        return Err(str(e))

    for fname in dir_files:
        if fname in excluded:
            continue

        p = path_join(dir, fname)
        if isdir(p):
            t = FileTree(path=p)
            tree.trees.append(t)
            r = build_file_tree(p, root, config, tree=t)
            if r.is_err():
                return Err(cast(errstr, r.err()))
        else:
            x = path_join(root, config.posts_dir)
            f = File(path=p, is_post=dir == x)
            tree.files.append(f)

    return Ok(tree)


def preprocess_md(
    state: State, file_metadata: FileMetadata, md: str
) -> Result[errstr, str]:
    def title(s: str) -> Result[errstr, str]:
        pat = '{{\s*(?:title)\s+"(.*)"\s*}}'
        m = re.search(pat, md)
        if m is None or m.lastindex != 1:
            return Err("Invalid match on 'title'")
        file_metadata.title = m[1]
        s = s.replace(m[0], "")
        s = f"# {m[1]}\n{s}"
        return Ok(s)

    def desc(s: str) -> Result[errstr, str]:
        pat = '{{\s*(?:desc)\s+"(.*)"\s*}}'
        m = re.search(pat, md)
        if m is None or m.lastindex != 1:
            return Err("Invalid match on 'desc'")

        file_metadata.desc = m[1]
        return Ok(s.replace(m[0], ""))

    def posts(s: str) -> Result[errstr, str]:
        pat = "{{\s*(?:posts)\s*}}"
        m = re.search(pat, md)
        if m is None:
            return Err("Invalid match on 'posts'")

        posts_html = ""
        for p in state.posts:
            url = p.path[p.path.find("/") :].replace(".md", ".html")
            posts_html += (
                '<div class="post">\n'
                '<div class="post-heading">\n'
                f'<a href="{url}">{p.metadata.title}</a>\n'
                "</div>\n"
                '<div class="post-desc">\n'
                f"{p.metadata.desc}\n"
                "</div>\n"
                "</div>\n"
            )

        posts_html = f'<div class="posts">{posts_html}</div>'
        return Ok(s.replace(m[0], posts_html))

    if "{{ title" in md or "{{title" in md:
        md = title(md).unwrap()
    if "{{ desc" in md or "{{desc" in md:
        md = desc(md).unwrap()
    if "{{ posts" in md or "{{posts" in md:
        md = posts(md).unwrap()
    if "[toc]" in md:
        md = md.replace("[toc]", "[TOC]")

    md = md.strip()

    return Ok(md)


def render_md(md: str) -> Result[errstr, str]:
    html = markdown.markdown(md, extensions=EXTENSIONS)
    return Ok(html)


if __name__ == "__main__":
    exit(main(argv))
