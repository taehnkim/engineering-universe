// Package prepare prototypes a single-pass native HTML cleanup and preview.
// It is intentionally opt-in until its output matches the Python DOM contract.
package prepare

import (
	"bytes"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"golang.org/x/net/html"
)

const marker = "data-eu-original-node-id"

var excluded = set("head", "script", "style", "noscript", "template")
var dropTags = set("aside", "audio", "button", "canvas", "dialog", "embed", "footer", "form", "head", "iframe", "img", "input", "nav", "noscript", "object", "picture", "script", "select", "source", "style", "svg", "template", "track", "video")
var emptyPrunable = set("div", "figure", "p", "section", "span")
var chromeRoles = set("alertdialog", "contentinfo", "dialog", "navigation", "search")
var chrome = regexp.MustCompile(`\b(?:backdrop|breadcrumb|comments?|consent|cookie|drawer|footer|lightbox|menu|modal|newsletter|overlay|pagination|popup|promo|recommend(?:ation|ations|ed)?|related|share|sidebar|social|subscribe|toast)\b`)
var extraction = regexp.MustCompile(`author|byline|date|publish|time|headline|title|subtitle|sub-title|subhead|standfirst|dek|excerpt|description|lead`)
var hiddenStyle = regexp.MustCompile(`(?i)(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)`)

type Prepared struct {
	CleanHTML   string `json:"clean_html"`
	PreviewHTML string `json:"preview_html"`
	NodeIDs     []int  `json:"node_ids"`
}

func set(names ...string) map[string]bool {
	out := make(map[string]bool, len(names))
	for _, name := range names {
		out[name] = true
	}
	return out
}

func attr(n *html.Node, key string) string {
	for _, a := range n.Attr {
		if a.Key == key {
			return a.Val
		}
	}
	return ""
}

func hasAttr(n *html.Node, key string) bool {
	for _, a := range n.Attr {
		if a.Key == key {
			return true
		}
	}
	return false
}

func assignIDs(n *html.Node, excludedAncestor bool, next *int) {
	if n.Type == html.ElementNode {
		if excludedAncestor || excluded[n.Data] {
			return
		}
		n.Attr = append(n.Attr, html.Attribute{Key: marker, Val: strconv.Itoa(*next)})
		*next++
	}
	for child := n.FirstChild; child != nil; child = child.NextSibling {
		assignIDs(child, excludedAncestor, next)
	}
}

func semanticName(raw string) string {
	var out strings.Builder
	var previous rune
	for _, current := range raw {
		if !unicode.IsLetter(current) && !unicode.IsDigit(current) {
			out.WriteByte(' ')
			previous = ' '
			continue
		}
		if unicode.IsUpper(current) && (unicode.IsLower(previous) || unicode.IsDigit(previous)) {
			out.WriteByte(' ')
		}
		out.WriteRune(unicode.ToLower(current))
		previous = current
	}
	return out.String()
}

func semanticValue(n *html.Node) string {
	var values strings.Builder
	values.WriteString(semanticName(n.Data))
	for _, key := range []string{"id", "class", "itemprop", "role", "aria-label"} {
		values.WriteByte(' ')
		values.WriteString(semanticName(attr(n, key)))
	}
	return values.String()
}

func isHidden(n *html.Node) bool {
	return hasAttr(n, "hidden") || strings.EqualFold(strings.TrimSpace(attr(n, "aria-hidden")), "true") || strings.EqualFold(strings.TrimSpace(attr(n, "aria-modal")), "true") || hiddenStyle.MatchString(attr(n, "style"))
}

func isChrome(n *html.Node) bool {
	if n.Data == "html" || n.Data == "body" {
		return false
	}
	role := strings.ToLower(attr(n, "role"))
	if chromeRoles[role] || isHidden(n) {
		return true
	}
	semantics := semanticValue(n)
	return !extraction.MatchString(semantics) && chrome.MatchString(semantics)
}

func removeChrome(n *html.Node) {
	for child := n.FirstChild; child != nil; {
		next := child.NextSibling
		if child.Type == html.ElementNode && (dropTags[child.Data] || isChrome(child)) {
			n.RemoveChild(child)
		} else {
			removeChrome(child)
		}
		child = next
	}
}

func hasElementChild(n *html.Node) bool {
	for child := n.FirstChild; child != nil; child = child.NextSibling {
		if child.Type == html.ElementNode {
			return true
		}
	}
	return false
}

func directText(n *html.Node) string {
	var out strings.Builder
	for child := n.FirstChild; child != nil; child = child.NextSibling {
		if child.Type == html.TextNode {
			out.WriteString(child.Data)
		}
	}
	return out.String()
}

func pruneEmpty(n *html.Node) {
	for child := n.FirstChild; child != nil; {
		next := child.NextSibling
		pruneEmpty(child)
		if child.Type == html.ElementNode && emptyPrunable[child.Data] && !hasElementChild(child) && strings.TrimSpace(directText(child)) == "" && !extraction.MatchString(semanticValue(child)) {
			n.RemoveChild(child)
		}
		child = next
	}
}

func collectIDs(n *html.Node, ids *[]int) {
	if n.Type == html.ElementNode {
		if value := attr(n, marker); value != "" {
			id, _ := strconv.Atoi(value)
			*ids = append(*ids, id)
		}
	}
	for child := n.FirstChild; child != nil; child = child.NextSibling {
		collectIDs(child, ids)
	}
}

func renameMarkers(n *html.Node) {
	for index := range n.Attr {
		if n.Attr[index].Key == marker {
			n.Attr[index].Key = "data-eu-node-id"
		}
	}
	for child := n.FirstChild; child != nil; child = child.NextSibling {
		renameMarkers(child)
	}
}

func render(n *html.Node) (string, error) {
	var out bytes.Buffer
	if err := html.Render(&out, n); err != nil {
		return "", err
	}
	return out.String(), nil
}

// Prepare parses, cleans, and renders once, retaining IDs assigned before cleanup.
func Prepare(raw string) (Prepared, error) {
	root, err := html.Parse(strings.NewReader(raw))
	if err != nil {
		return Prepared{}, fmt.Errorf("parse HTML: %w", err)
	}
	next := 0
	assignIDs(root, false, &next)
	removeChrome(root)
	pruneEmpty(root)
	ids := make([]int, 0, next)
	collectIDs(root, &ids)
	clean, err := render(root)
	if err != nil {
		return Prepared{}, err
	}
	renameMarkers(root)
	preview, err := render(root)
	if err != nil {
		return Prepared{}, err
	}
	return Prepared{CleanHTML: clean, PreviewHTML: preview, NodeIDs: ids}, nil
}
