import re
import collections

from . import util
from sqlalchemy import util as sqlautil


_relative_destination = re.compile(r'(?:\+|-)\d+')


class RevisionMap(object):
    """Maintains a map of :class:`.Revision` objects.

    :class:`.RevisionMap` is used by :class:`.ScriptDirectory` to maintain
    and traverse the collection of :class:`.Script` objects, which are
    themselves instances of :class:`.Revision`.

    """

    def __init__(self, generator):
        self._generator = generator

    @util.memoized_property
    def heads(self):
        """All "head" revisions as strings.

        This is normally a tuple of length one,
        unless unmerged branches are present.

        :return: a tuple of string revision numbers.

        """
        self._revision_map
        return self.heads

    @util.memoized_property
    def bases(self):
        """All "base" revisions as  strings.

        These are revisions that have a ``down_revision`` of None,
        or empty tuple.

        :return: a tuple of string revision numbers.

        """
        self._revision_map
        return self.bases

    @util.memoized_property
    def _revision_map(self):
        map_ = {}

        heads = sqlautil.OrderedSet()
        self.bases = ()

        for revision in self._generator():
            if revision.revision in map_:
                util.warn("Revision %s is present more than once" %
                          revision.revision)
            map_[revision.revision] = revision
            heads.add(revision.revision)
            if revision.is_base:
                self.bases += (revision.revision, )

        for rev in map_.values():
            for downrev in rev.down_revision:
                if downrev not in map_:
                    util.warn("Revision %s referenced from %s is not present"
                              % (rev.down_revision, rev))
                map_[downrev].add_nextrev(rev.revision)
                heads.discard(downrev)
        map_[None] = map_[()] = None
        self.heads = tuple(heads)
        return map_

    def get_current_head(self):
        """Return the current head revision.

        If the script directory has multiple heads
        due to branching, an error is raised;
        :meth:`.ScriptDirectory.get_heads` should be
        preferred.

        :return: a string revision number.

        .. seealso::

            :meth:`.ScriptDirectory.get_heads`

        """
        current_heads = self.heads
        if len(current_heads) > 1:
            raise util.CommandError(
                'The script directory has multiple heads (due to branching).'
                'Please use get_heads(), or merge the branches using '
                'alembic merge.')

        if current_heads:
            return current_heads[0]
        else:
            return None

    def get_symbolic_revisions(self, id_):
        """Return revisions based on a possibly "symbolic" revision,
        such as "head" or "base".

        If an exact revision number is given, a tuple of one :class:`.Revision`
        is returned.  If "head" or "base" is given, a tuple of potentially
        multiple heads or bases is returned.

        Supports partial identifiers, where the given identifier
        is matched against all identifiers that start with the given
        characters; if there is exactly one match, that determines the
        full revision.

        """
        if id_ == 'head':
            return tuple(self.get_revision(head) for head in self.heads)
        elif id_ == 'base':
            return tuple(self.get_revision(base) for base in self.bases)
        else:
            return tuple(self.get_revision(ident)
                         for ident in util.to_tuple(id_, ()))

    def get_revision(self, id_):
        """Return the :class:`.Revision` instance with the given rev id.

        Supports partial identifiers, where the given identifier
        is matched against all identifiers that start with the given
        characters; if there is exactly one match, that determines the
        full revision.

        """

        try:
            return self._revision_map[id_]
        except KeyError:
            # do a partial lookup
            revs = [x for x in self._revision_map
                    if x and x.startswith(id_)]
            if not revs:
                raise util.CommandError("No such revision '%s'" % id_)
            elif len(revs) > 1:
                raise util.CommandError(
                    "Multiple revisions start "
                    "with '%s': %s..." % (
                        id_,
                        ", ".join("'%s'" % r for r in revs[0:3])
                    ))
            else:
                return self._revision_map[revs[0]]

    def iterate_revisions(self, upper, lower):
        """Iterate through script revisions, starting at the given
        upper revision identifier and ending at the lower.

        The traversal uses strictly the `down_revision`
        marker inside each migration script, so
        it is a requirement that upper >= lower,
        else you'll get nothing back.

        The iterator yields :class:`.Revision` objects.

        """
        if upper is not None and _relative_destination.match(upper):
            relative = int(upper)
            revs = list(self._iterate_revisions("head", lower))
            revs = revs[-relative:]
            if len(revs) != abs(relative):
                raise util.CommandError(
                    "Relative revision %s didn't "
                    "produce %d migrations" % (upper, abs(relative)))
            return iter(revs)
        elif lower is not None and _relative_destination.match(lower):
            relative = int(lower)
            revs = list(self._iterate_revisions(upper, "base"))
            revs = revs[0:-relative]
            if len(revs) != abs(relative):
                raise util.CommandError(
                    "Relative revision %s didn't "
                    "produce %d migrations" % (lower, abs(relative)))
            return iter(revs)
        else:
            return self._iterate_revisions(upper, lower)

    def _get_descendant_nodes(self, targets):
        todo = collections.deque(targets)
        while todo:
            rev = todo.pop()
            todo.extend(
                self._revision_map[rev_id] for rev_id in rev.nextrev)
            yield rev

    def _get_ancestor_nodes(self, targets):
        todo = collections.deque(targets)
        while todo:
            rev = todo.pop()
            todo.extend(
                self._revision_map[rev_id] for rev_id in rev.down_revision)
            yield rev

    def _iterate_revisions(self, upper, lower):
        """iterate revisions from upper to lower.

        The traversal is depth-first within branches, and breadth-first
        across branches as a whole.

        """
        lower = self.get_symbolic_revisions(lower)
        uppers = self.get_symbolic_revisions(upper)

        total_space = set(
            rev.revision
            for rev in self._get_ancestor_nodes(uppers)
        ).intersection(
            rev.revision for rev in self._get_descendant_nodes(lower)
        )

        branch_endpoints = set(
            rev.revision for rev in
            (self._revision_map[rev] for rev in total_space)
            if rev.is_branch_point and
            len(total_space.intersection(rev.nextrev)) > 1
        )

        todo = collections.deque(uppers)
        stop = set(lower)
        while todo:
            stop.update(
                rev.revision for rev in todo
                if rev.revision in branch_endpoints)
            rev = todo.popleft()

            # do depth first for elements within the branches
            todo.extendleft([
                self._revision_map[downrev]
                for downrev in reversed(rev.down_revision)
                if downrev not in branch_endpoints and downrev not in stop
                and downrev in total_space])

            # then put the actual branch points at the end of the
            # list for subsequent traversal
            todo.extend([
                self._revision_map[downrev]
                for downrev in rev.down_revision
                if downrev in branch_endpoints and downrev not in stop
                and downrev in total_space
            ])
            yield rev


class Revision(object):
    """Base class for revisioned objects.

    The :class:`.Revision` class is the base of the more public-facing
    :class:`.Script` object, which represents a migration script.
    The mechanics of revision management and traversal are encapsulated
    within :class:`.Revision`, while :class:`.Script` applies this logic
    to Python files in a version directory.

    """
    nextrev = frozenset()

    revision = None
    """The string revision number."""

    down_revision = None
    """The ``down_revision`` identifier(s) within the migration script."""

    def __init__(self, revision, down_revision):
        self.revision = revision
        self.down_revision = down_revision

    def add_nextrev(self, rev):
        self.nextrev = self.nextrev.union([rev])

    @property
    def is_head(self):
        """Return True if this :class:`.Revision` is a 'head' revision.

        This is determined based on whether any other :class:`.Script`
        within the :class:`.ScriptDirectory` refers to this
        :class:`.Script`.   Multiple heads can be present.

        """
        return not bool(self.nextrev)

    @property
    def is_base(self):
        """Return True if this :class:`.Revision` is a 'base' revision."""

        return self.down_revision in (None, ())

    @property
    def is_branch_point(self):
        """Return True if this :class:`.Script` is a branch point.

        A branchpoint is defined as a :class:`.Script` which is referred
        to by more than one succeeding :class:`.Script`, that is more
        than one :class:`.Script` has a `down_revision` identifier pointing
        here.

        """
        return len(self.nextrev) > 1

    @property
    def is_merge_point(self):
        """Return True if this :class:`.Script` is a merge point."""

        return len(self.down_revision) > 1