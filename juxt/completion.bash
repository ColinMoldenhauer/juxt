# Bash completion for juxt.  Enable it with
#
#     eval "$(juxt --bash-completion)"        # in ~/.bashrc
#
# zsh can reuse the same function through bashcompinit:
#
#     autoload -U bashcompinit && bashcompinit
#     eval "$(juxt --bash-completion)"
#
# Path arguments complete with {placeholders} left in place, exactly like the
# :pattern command bar inside the app: juxt plots/{sensor}/2<TAB> lists what
# every sensor directory holds, so a template can be built top-down.
#
# Candidates come from juxt-complete when it is on PATH (it starts in
# milliseconds because it never imports Qt), otherwise from juxt itself.
# Set JUXT_COMPLETE to the full path of the helper when juxt lives in a
# virtualenv that is not active.

# Fill _juxt_complete_reply with the candidates for word $2, preceded by $1.
# Returns non-zero when no helper could answer, so callers fall back.
_juxt_complete_words() {
    local prev=$1 cur=$2 out line
    local -a cmd=()

    if [[ -n ${JUXT_COMPLETE:-} ]]; then
        cmd=("$JUXT_COMPLETE" --)
    elif command -v juxt-complete >/dev/null 2>&1; then
        cmd=(juxt-complete --)
    elif command -v juxt >/dev/null 2>&1; then
        cmd=(juxt --complete-words)
    else
        return 1
    fi

    out=$("${cmd[@]}" "$prev" "$cur" 2>/dev/null) || return 1

    _juxt_complete_reply=()
    while IFS= read -r line; do
        # A helper on Windows may answer with CRLF -- an older build, or one
        # from a different install.  A candidate must never carry the CR onto
        # the command line, so drop it here as well as at the source.
        line=${line%$'\r'}
        [[ -n $line ]] && _juxt_complete_reply+=("$line")
    done <<< "$out"
    return 0
}

_juxt_completion() {
    local cur prev line
    local -a _juxt_complete_reply=()

    cur=${COMP_WORDS[COMP_CWORD]}
    prev=""
    ((COMP_CWORD > 0)) && prev=${COMP_WORDS[COMP_CWORD - 1]}

    COMPREPLY=()
    if ! _juxt_complete_words "$prev" "$cur"; then
        # No helper, or one too old to understand us -- plain filenames.
        while IFS= read -r line; do COMPREPLY+=("$line"); done \
            < <(compgen -f -- "$cur")
        return
    fi

    COMPREPLY=("${_juxt_complete_reply[@]}")

    # -o nospace keeps the word open where completion stopped at a boundary --
    # a directory, or a token separator before the next {placeholder}.  Anything
    # else is a finished word and gets its space.
    if ((${#COMPREPLY[@]} == 1)) && [[ ${COMPREPLY[0]} != *[/_.-] ]]; then
        COMPREPLY[0]+=" "
    fi
}

complete -o nospace -F _juxt_completion juxt

# ble.sh takes the word apart before a compspec sees it: a {placeholder} is
# read as a brace expansion, so the word juxt is asked to complete has lost its
# braces and every candidate we return is filtered out again.  ble.sh offers
# its own extension point, which does see the word as typed, so use that when
# it is loaded.  These definitions are inert under plain bash.

# Candidates are inserted verbatim -- no quoting of the braces -- and only a
# finished word is closed with a space.  A candidate ending on a separator is
# an invitation to keep typing: a directory, or the boundary in front of the
# next {placeholder}.
function ble/complete/action:juxt/initialize { return 0; }
function ble/complete/action:juxt/initialize.batch { inserts=("${cands[@]}"); }
function ble/complete/action:juxt/complete {
    [[ $CAND == *[/_.-] ]] || ble/complete/action/complete.addtail ' '
}
function ble/complete/action:juxt/get-desc { ble/complete/action:plain/get-desc; }

function ble/cmdinfo/complete:juxt {
    local cur=$COMPS prev=""
    ((${comp_cword:-0} > 0)) && prev=${comp_words[comp_cword - 1]}

    local -a _juxt_complete_reply=()
    _juxt_complete_words "$prev" "$cur" || return 1
    ((${#_juxt_complete_reply[@]})) || return 1

    # Show bare names in the menu, the way file completion does.
    local COMP_PREFIX=
    [[ $cur == */* ]] && COMP_PREFIX=${cur%/*}/

    local flag_source_filter=1   # the helper already filtered by prefix
    local word
    for word in "${_juxt_complete_reply[@]}"; do
        ble/complete/cand/yield juxt "$word"
    done
    return 0
}
