# Copyright 2012 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Classes and methods to create and manage Courses."""

__author__ = 'Pavel Simakov (psimakov@google.com)'

import datetime
import os
import urllib
import appengine_config
from common import jinja_utils
from common import safe_dom
from controllers import sites
from controllers.utils import ApplicationHandler
from controllers.utils import HUMAN_READABLE_TIME_FORMAT
from controllers.utils import ReflectiveRequestHandler
import jinja2
import jinja2.exceptions
from models import config
from models import courses
from models import custom_modules
from models import jobs
from models import roles
from models import transforms
from models import utils
from models import vfs
from models.models import QuestionDAO
from models.models import QuestionGroupDAO
from models.models import Student
from tools import verify
from course_settings import CourseSettingsHandler
from course_settings import CourseSettingsRESTHandler
import filer
from filer import AssetItemRESTHandler
from filer import AssetUriRESTHandler
from filer import FileManagerAndEditor
from filer import FilesItemRESTHandler
from filer import TextAssetRESTHandler
import messages
from peer_review import AssignmentManager
from question_editor import McQuestionRESTHandler
from question_editor import QuestionManagerAndEditor
from question_editor import SaQuestionRESTHandler
from question_group_editor import QuestionGroupManagerAndEditor
from question_group_editor import QuestionGroupRESTHandler
import unit_lesson_editor
from unit_lesson_editor import AssessmentRESTHandler
from unit_lesson_editor import ExportAssessmentRESTHandler
from unit_lesson_editor import ImportActivityRESTHandler
from unit_lesson_editor import ImportCourseRESTHandler
from unit_lesson_editor import LessonRESTHandler
from unit_lesson_editor import LinkRESTHandler
from unit_lesson_editor import UnitLessonEditor
from unit_lesson_editor import UnitLessonTitleRESTHandler
from unit_lesson_editor import UnitRESTHandler
from google.appengine.api import users


class DashboardHandler(
    CourseSettingsHandler, FileManagerAndEditor, UnitLessonEditor,
    QuestionManagerAndEditor, QuestionGroupManagerAndEditor, AssignmentManager,
    ApplicationHandler, ReflectiveRequestHandler):
    """Handles all pages and actions required for managing a course."""

    default_action = 'outline'
    get_actions = [
        default_action, 'assets', 'settings', 'analytics',
        'edit_basic_settings', 'edit_settings', 'edit_unit_lesson',
        'edit_unit', 'edit_link', 'edit_lesson', 'edit_assessment',
        'add_asset', 'delete_asset', 'manage_text_asset', 'import_course',
        'edit_assignment', 'add_mc_question', 'add_sa_question',
        'edit_question', 'add_question_group', 'edit_question_group']
    # Requests to these handlers automatically go through an XSRF token check
    # that is implemented in ReflectiveRequestHandler.
    post_actions = [
        'compute_student_stats', 'create_or_edit_settings', 'add_unit',
        'add_link', 'add_assessment', 'add_lesson',
        'edit_basic_course_settings', 'add_reviewer', 'delete_reviewer']

    local_fs = vfs.LocalReadOnlyFileSystem(logical_home_folder='/')

    @classmethod
    def get_child_routes(cls):
        """Add child handlers for REST."""
        return [
            (AssessmentRESTHandler.URI, AssessmentRESTHandler),
            (AssetItemRESTHandler.URI, AssetItemRESTHandler),
            (CourseSettingsRESTHandler.URI, CourseSettingsRESTHandler),
            (FilesItemRESTHandler.URI, FilesItemRESTHandler),
            (AssetItemRESTHandler.URI, AssetItemRESTHandler),
            (AssetUriRESTHandler.URI, AssetUriRESTHandler),
            (ImportActivityRESTHandler.URI, ImportActivityRESTHandler),
            (ImportCourseRESTHandler.URI, ImportCourseRESTHandler),
            (LessonRESTHandler.URI, LessonRESTHandler),
            (LinkRESTHandler.URI, LinkRESTHandler),
            (UnitLessonTitleRESTHandler.URI, UnitLessonTitleRESTHandler),
            (UnitRESTHandler.URI, UnitRESTHandler),
            (McQuestionRESTHandler.URI, McQuestionRESTHandler),
            (SaQuestionRESTHandler.URI, SaQuestionRESTHandler),
            (TextAssetRESTHandler.URI, TextAssetRESTHandler),
            (QuestionGroupRESTHandler.URI, QuestionGroupRESTHandler),
            (ExportAssessmentRESTHandler.URI, ExportAssessmentRESTHandler)
        ]

    def can_view(self):
        """Checks if current user has viewing rights."""
        return roles.Roles.is_course_admin(self.app_context)

    def can_edit(self):
        """Checks if current user has editing rights."""
        return roles.Roles.is_course_admin(self.app_context)

    def get(self):
        """Enforces rights to all GET operations."""
        if not self.can_view():
            self.redirect(self.app_context.get_slug())
            return
        # Force reload of properties. It is expensive, but admin deserves it!
        config.Registry.get_overrides(force_update=True)
        return super(DashboardHandler, self).get()

    def post(self):
        """Enforces rights to all POST operations."""
        if not self.can_edit():
            self.redirect(self.app_context.get_slug())
            return
        return super(DashboardHandler, self).post()

    def get_template(self, template_name, dirs):
        """Sets up an environment and Gets jinja template."""
        return jinja_utils.get_template(
            template_name, dirs + [os.path.dirname(__file__)], handler=self)

    def _get_alerts(self):
        alerts = []
        if not courses.is_editable_fs(self.app_context):
            alerts.append('Read-only course.')
        if not self.app_context.now_available:
            alerts.append('The course is not publicly available.')
        return '\n'.join(alerts)

    def _get_top_nav(self):
        current_action = self.request.get('action')
        nav_mappings = [
            ('', 'Outline'),
            ('assets', 'Assets'),
            ('settings', 'Settings'),
            ('analytics', 'Analytics'),
            ('edit_assignment', 'Peer Review')]
        nav = safe_dom.NodeList()
        for action, title in nav_mappings:

            class_name = 'selected' if action == current_action else ''
            action_href = 'dashboard?action=%s' % action
            nav.append(safe_dom.Element(
                'a', href=action_href, className=class_name).add_text(
                    title))

        if roles.Roles.is_super_admin():
            nav.append(safe_dom.Element(
                'a', href='/admin').add_text('Admin'))

        nav.append(safe_dom.Element(
            'a', href='https://code.google.com/p/course-builder/wiki/Dashboard',
            target='_blank').add_text('Help'))

        return nav

    def render_page(self, template_values):
        """Renders a page using provided template values."""

        template_values['top_nav'] = self._get_top_nav()
        template_values['gcb_course_base'] = self.get_base_href(self)
        template_values['user_nav'] = safe_dom.NodeList().append(
            safe_dom.Text('%s | ' % users.get_current_user().email())
        ).append(
            safe_dom.Element(
                'a', href=users.create_logout_url(self.request.uri)
            ).add_text('Logout'))
        template_values[
            'page_footer'] = 'Created on: %s' % datetime.datetime.now()

        if not template_values.get('sections'):
            template_values['sections'] = []

        self.response.write(
            self.get_template('view.html', []).render(template_values))

    def format_title(self, text):
        """Formats standard title."""
        title = self.app_context.get_environ()['course']['title']
        return safe_dom.NodeList().append(
            safe_dom.Text('Course Builder ')
        ).append(
            safe_dom.Entity('&gt;')
        ).append(
            safe_dom.Text(' %s ' % title)
        ).append(
            safe_dom.Entity('&gt;')
        ).append(
            safe_dom.Text(' Dashboard ')
        ).append(
            safe_dom.Entity('&gt;')
        ).append(
            safe_dom.Text(' %s' % text)
        )

    def _get_edit_link(self, url):
        return safe_dom.NodeList().append(
            safe_dom.Text(' ')
        ).append(
            safe_dom.Element('a', href=url).add_text('Edit')
        )

    def _get_availability(self, resource):
        if not hasattr(resource, 'now_available'):
            return safe_dom.Text('')
        if resource.now_available:
            return safe_dom.Text('')
        else:
            return safe_dom.NodeList().append(
                safe_dom.Text(' ')
            ).append(
                safe_dom.Element(
                    'span', className='draft-label'
                ).add_text('(%s)' % unit_lesson_editor.DRAFT_TEXT)
            )

    def render_course_outline_to_html(self):
        """Renders course outline to HTML."""
        course = courses.Course(self)
        if not course.get_units():
            return []

        is_editable = filer.is_editable_fs(self.app_context)

        lines = safe_dom.Element('ul', style='list-style: none;')
        for unit in course.get_units():
            if unit.type == verify.UNIT_TYPE_ASSESSMENT:
                li = safe_dom.Element('li').add_child(
                    safe_dom.Element(
                        'a', href='assessment?name=%s' % unit.unit_id,
                        className='strong'
                    ).add_text(unit.title)
                ).add_child(self._get_availability(unit))
                if is_editable:
                    url = self.canonicalize_url(
                        '/dashboard?%s') % urllib.urlencode({
                            'action': 'edit_assessment',
                            'key': unit.unit_id})
                    li.add_child(self._get_edit_link(url))
                lines.add_child(li)
                continue

            if unit.type == verify.UNIT_TYPE_LINK:
                li = safe_dom.Element('li').add_child(
                    safe_dom.Element(
                        'a', href=unit.href, className='strong'
                    ).add_text(unit.title)
                ).add_child(self._get_availability(unit))
                if is_editable:
                    url = self.canonicalize_url(
                        '/dashboard?%s') % urllib.urlencode({
                            'action': 'edit_link',
                            'key': unit.unit_id})
                    li.add_child(self._get_edit_link(url))
                lines.add_child(li)
                continue

            if unit.type == verify.UNIT_TYPE_UNIT:
                li = safe_dom.Element('li').add_child(
                    safe_dom.Element(
                        'a', href='unit?unit=%s' % unit.unit_id,
                        className='strong').add_text(
                            'Unit %s - %s' % (unit.index, unit.title))
                ).add_child(self._get_availability(unit))
                if is_editable:
                    url = self.canonicalize_url(
                        '/dashboard?%s') % urllib.urlencode({
                            'action': 'edit_unit',
                            'key': unit.unit_id})
                    li.add_child(self._get_edit_link(url))

                ol = safe_dom.Element('ol')
                for lesson in course.get_lessons(unit.unit_id):
                    li2 = safe_dom.Element('li').add_child(
                        safe_dom.Element(
                            'a',
                            href='unit?unit=%s&lesson=%s' % (
                                unit.unit_id, lesson.lesson_id),
                        ).add_text(lesson.title)
                    ).add_child(self._get_availability(lesson))
                    if is_editable:
                        url = self.get_action_url(
                            'edit_lesson', key=lesson.lesson_id)
                        li2.add_child(self._get_edit_link(url))
                    ol.add_child(li2)
                li.add_child(ol)
                lines.add_child(li)
                continue

            raise Exception('Unknown unit type: %s.' % unit.type)

        return lines

    def get_outline(self):
        """Renders course outline view."""

        pages_info = [
            safe_dom.Element(
                'a', href=self.canonicalize_url('/announcements')
            ).add_text('Announcements'),
            safe_dom.Element(
                'a', href=self.canonicalize_url('/course')
            ).add_text('Course')]

        outline_actions = []
        if filer.is_editable_fs(self.app_context):
            outline_actions.append({
                'id': 'edit_unit_lesson',
                'caption': 'Organize',
                'href': self.get_action_url('edit_unit_lesson')})
            all_units = courses.Course(self).get_units()
            if any([unit.type == verify.UNIT_TYPE_UNIT for unit in all_units]):
                outline_actions.append({
                    'id': 'add_lesson',
                    'caption': 'Add Lesson',
                    'action': self.get_action_url('add_lesson'),
                    'xsrf_token': self.create_xsrf_token('add_lesson')})
            outline_actions.append({
                'id': 'add_unit',
                'caption': 'Add Unit',
                'action': self.get_action_url('add_unit'),
                'xsrf_token': self.create_xsrf_token('add_unit')})
            outline_actions.append({
                'id': 'add_link',
                'caption': 'Add Link',
                'action': self.get_action_url('add_link'),
                'xsrf_token': self.create_xsrf_token('add_link')})
            outline_actions.append({
                'id': 'add_assessment',
                'caption': 'Add Assessment',
                'action': self.get_action_url('add_assessment'),
                'xsrf_token': self.create_xsrf_token('add_assessment')})
            if not courses.Course(self).get_units():
                outline_actions.append({
                    'id': 'import_course',
                    'caption': 'Import',
                    'href': self.get_action_url('import_course')
                    })

        data_info = self.list_files('/data/')

        sections = [
            {
                'title': 'Pages',
                'description': messages.PAGES_DESCRIPTION,
                'children': pages_info},
            {
                'title': 'Course Outline',
                'description': messages.COURSE_OUTLINE_DESCRIPTION,
                'actions': outline_actions,
                'pre': self.render_course_outline_to_html()},
            {
                'title': 'Data Files',
                'description': messages.DATA_FILES_DESCRIPTION,
                'children': data_info}]

        template_values = {}
        template_values['page_title'] = self.format_title('Outline')
        template_values['alerts'] = self._get_alerts()
        template_values['sections'] = sections
        self.render_page(template_values)

    def get_action_url(self, action, key=None, extra_args=None):
        args = {'action': action}
        if key:
            args['key'] = key
        if extra_args:
            args.update(extra_args)
        url = '/dashboard?%s' % urllib.urlencode(args)
        return self.canonicalize_url(url)

    def get_settings(self):
        """Renders course settings view."""

        yaml_actions = []
        basic_setting_actions = []

        # Basic course info.
        course_info = [
            'Course Title: %s' % self.app_context.get_environ()['course'][
                'title'],
            'Context Path: %s' % self.app_context.get_slug(),
            'Datastore Namespace: %s' % self.app_context.get_namespace_name()]

        # Course file system.
        fs = self.app_context.fs.impl
        course_info.append(('File System: %s' % fs.__class__.__name__))
        if fs.__class__ == vfs.LocalReadOnlyFileSystem:
            course_info.append(('Home Folder: %s' % sites.abspath(
                self.app_context.get_home_folder(), '/')))

        # Enable editing if supported.
        if filer.is_editable_fs(self.app_context):
            yaml_actions.append({
                'id': 'edit_course_yaml',
                'caption': 'Advanced Edit',
                'action': self.get_action_url('create_or_edit_settings'),
                'xsrf_token': self.create_xsrf_token(
                    'create_or_edit_settings')})
            yaml_actions.append({
                'id': 'edit_basic_course_settings',
                'caption': 'Edit',
                'action': self.get_action_url('edit_basic_course_settings'),
                'xsrf_token': self.create_xsrf_token(
                    'edit_basic_course_settings')})

        # course.yaml file content.
        yaml_info = []
        yaml_stream = self.app_context.fs.open(
            self.app_context.get_config_filename())
        if yaml_stream:
            yaml_lines = yaml_stream.read().decode('utf-8')
            for line in yaml_lines.split('\n'):
                yaml_info.append(line)
        else:
            yaml_info.append('< empty file >')

        # course_template.yaml file contents
        course_template_info = []
        course_template_stream = open(os.path.join(os.path.dirname(
            __file__), '../../course_template.yaml'), 'r')
        if course_template_stream:
            course_template_lines = course_template_stream.read().decode(
                'utf-8')
            for line in course_template_lines.split('\n'):
                course_template_info.append(line)
        else:
            course_template_info.append('< empty file >')

        # Prepare template values.
        template_values = {}
        template_values['page_title'] = self.format_title('Settings')
        template_values['page_description'] = messages.SETTINGS_DESCRIPTION
        template_values['sections'] = [
            {
                'title': 'About the Course',
                'description': messages.ABOUT_THE_COURSE_DESCRIPTION,
                'actions': basic_setting_actions,
                'children': course_info},
            {
                'title': 'Contents of course.yaml file',
                'description': messages.CONTENTS_OF_THE_COURSE_DESCRIPTION,
                'actions': yaml_actions,
                'children': yaml_info},
            {
                'title': 'Contents of course_template.yaml file',
                'description': messages.COURSE_TEMPLATE_DESCRIPTION,
                'children': course_template_info}]

        self.render_page(template_values)

    def list_files(self, subfolder, merge_local_files=False):
        """Makes a list of files in a subfolder.

        Args:
            subfolder: string. Relative path of the subfolder to list.
            merge_local_files: boolean. If True, the returned list will
                contain files found on either the datastore filesystem or the
                read-only local filesystem. If a file is found on both, its
                datastore filesystem version will trump its local filesystem
                version.

        Returns:
            List of relative, normalized file path strings.
        """
        home = sites.abspath(self.app_context.get_home_folder(), '/')
        all_paths = set(self.app_context.fs.list(
            sites.abspath(self.app_context.get_home_folder(), subfolder)))

        if merge_local_files:
            all_paths = all_paths.union(set([
                os.path.join(appengine_config.BUNDLE_ROOT, path) for path in
                self.local_fs.list(subfolder[1:])]))

        result = []
        for abs_filename in all_paths:
            filename = os.path.relpath(abs_filename, home)
            result.append(vfs.AbstractFileSystem.normpath(filename))
        return sorted(result)

    def list_and_format_file_list(
        self, title, subfolder,
        links=False, upload=False, prefix=None, caption_if_empty='< none >',
        edit_url_template=None, merge_local_files=False, sub_title=None):
        """Walks files in folders and renders their names in a section."""

        items = safe_dom.NodeList()
        count = 0
        for filename in self.list_files(
                subfolder, merge_local_files=merge_local_files):
            if prefix and not filename.startswith(prefix):
                continue
            li = safe_dom.Element('li')
            if links:
                li.add_child(safe_dom.Element(
                    'a', href=urllib.quote(filename)).add_text(filename))
            else:
                li.add_text(filename)
            if (edit_url_template and
                self.app_context.fs.impl.is_read_write()):
                edit_url = edit_url_template % urllib.quote(filename)
                li.add_child(
                    safe_dom.Entity('&nbsp;')
                ).add_child(
                    safe_dom.Element('a', href=edit_url).add_text('[Edit]'))
            count += 1
            items.append(li)

        output = safe_dom.NodeList()

        if filer.is_editable_fs(self.app_context) and upload:
            output.append(
                safe_dom.Element(
                    'a', className='gcb-button gcb-pull-right',
                    href='dashboard?%s' % urllib.urlencode(
                        {'action': 'add_asset', 'base': subfolder})
                ).add_text(
                    'Upload to ' +
                    filer.strip_leading_and_trailing_slashes(subfolder))
            ).append(
                safe_dom.Element('div', style='clear: both; padding-top: 2px;'))
        if title:
            h3 = safe_dom.Element('h3')
            if count:
                h3.add_text('%s (%s)' % (title, count))
            else:
                h3.add_text(title)
            output.append(h3)
        if sub_title:
            output.append(safe_dom.Element('blockquote').add_text(sub_title))
        if items:
            output.append(safe_dom.Element('ol').add_children(items))
        else:
            if caption_if_empty:
                output.append(
                    safe_dom.Element('blockquote').add_text(caption_if_empty))
        return output

    def list_questions(self):
        """Prepare a list of the question bank contents."""
        if not filer.is_editable_fs(self.app_context):
            return safe_dom.NodeList()

        output = safe_dom.NodeList().append(
            safe_dom.Element(
                'a', className='gcb-button gcb-pull-right',
                href='dashboard?action=add_mc_question'
            ).add_text('Add Multiple Choice')
        ).append(
            safe_dom.Element(
                'a', className='gcb-button gcb-pull-right',
                href='dashboard?action=add_sa_question'
            ).add_text('Add Short Answer')
        ).append(
            safe_dom.Element('div', style='clear: both; padding-top: 2px;')
        ).append(
            safe_dom.Element('h3').add_text('Question Bank')
        )

        all_questions = QuestionDAO.get_all()
        if all_questions:
            ol = safe_dom.Element('ol')
            for question in all_questions:
                edit_url = 'dashboard?action=edit_question&key=%s' % question.id
                li = safe_dom.Element('li')
                li.add_text(
                    question.description
                ).add_child(
                    safe_dom.Entity('&nbsp;')
                ).add_child(
                    safe_dom.Element('a', href=edit_url).add_text('[Edit]'))
                ol.add_child(li)
            output.append(ol)
        else:
            output.append(safe_dom.Element('blockquote').add_text('< none >'))

        return output

    def list_question_groups(self):
        """Prepare a list of question groups."""
        if not filer.is_editable_fs(self.app_context):
            return safe_dom.NodeList()

        all_questions = QuestionDAO.get_all()
        output = safe_dom.NodeList()
        if all_questions:
            output.append(
                safe_dom.Element(
                    'a', className='gcb-button gcb-pull-right',
                    href='dashboard?action=add_question_group'
                ).add_text('Add Question Group')
            ).append(
                safe_dom.Element('div', style='clear: both; padding-top: 2px;'))
        output.append(
            safe_dom.Element('h3').add_text('Question Groups')
        )

        # TODO(jorr): Hook this into the datastore
        all_question_groups = QuestionGroupDAO.get_all()
        if all_question_groups:
            ol = safe_dom.Element('ol')
            for question_group in all_question_groups:
                edit_url = 'dashboard?action=edit_question_group&key=%s' % (
                    question_group.id)
                li = safe_dom.Element('li')
                li.add_text(
                    question_group.description
                ).add_child(
                    safe_dom.Entity('&nbsp;')
                ).add_child(
                    safe_dom.Element('a', href=edit_url).add_text('[Edit]'))
                ol.add_child(li)
            output.append(ol)
        else:
            output.append(safe_dom.Element('blockquote').add_text('< none >'))

        return output

    def get_assets(self):
        """Renders course assets view."""

        def inherits_from(folder):
            return '< inherited from %s >' % folder

        text_asset_url_template = 'dashboard?action=manage_text_asset&uri=%s'

        items = safe_dom.NodeList().append(
            self.list_questions()
        ).append(
            self.list_question_groups()
        ).append(
            self.list_and_format_file_list(
                'Assessments', '/assets/js/', links=True,
                prefix='assets/js/assessment-')
        ).append(
            self.list_and_format_file_list(
                'Activities', '/assets/js/', links=True,
                prefix='assets/js/activity-')
        ).append(
            self.list_and_format_file_list(
                'Images & Documents', '/assets/img/', links=True, upload=True,
                edit_url_template='dashboard?action=delete_asset&uri=%s',
                caption_if_empty=inherits_from('/assets/img/'))
        ).append(
            self.list_and_format_file_list(
                'Cascading Style Sheets', '/assets/css/', links=True,
                upload=True, edit_url_template=text_asset_url_template,
                caption_if_empty=inherits_from('/assets/css/'),
                merge_local_files=True)
        ).append(
            self.list_and_format_file_list(
                'JavaScript Libraries', '/assets/lib/', links=True,
                upload=True, edit_url_template=text_asset_url_template,
                caption_if_empty=inherits_from('/assets/lib/'),
                merge_local_files=True)
        ).append(
            self.list_and_format_file_list(
                'View Templates', '/views/', upload=True,
                edit_url_template=text_asset_url_template,
                caption_if_empty=inherits_from('/views/'),
                merge_local_files=True)
        )

        template_values = {}
        template_values['page_title'] = self.format_title('Assets')
        template_values['page_description'] = messages.ASSETS_DESCRIPTION
        template_values['main_content'] = items
        self.render_page(template_values)

    def get_markup_for_basic_analytics(self, job):
        """Renders markup for basic enrollment and assessment analytics."""
        subtemplate_values = {}
        errors = []
        stats_calculated = False
        update_message = safe_dom.Text('')

        if not job:
            update_message = safe_dom.Text(
                'Enrollment/assessment statistics have not been calculated '
                'yet.')
        else:
            if job.status_code == jobs.STATUS_CODE_COMPLETED:
                stats = transforms.loads(job.output)
                stats_calculated = True

                subtemplate_values['enrolled'] = stats['enrollment']['enrolled']
                subtemplate_values['unenrolled'] = (
                    stats['enrollment']['unenrolled'])

                scores = []
                total_records = 0
                for key, value in stats['scores'].items():
                    total_records += value[0]
                    avg = round(value[1] / value[0], 1) if value[0] else 0
                    scores.append({'key': key, 'completed': value[0],
                                   'avg': avg})
                subtemplate_values['scores'] = scores
                subtemplate_values['total_records'] = total_records

                update_message = safe_dom.Text("""
                    Enrollment and assessment statistics were last updated at
                    %s in about %s second(s).""" % (
                        job.updated_on.strftime(HUMAN_READABLE_TIME_FORMAT),
                        job.execution_time_sec))
            elif job.status_code == jobs.STATUS_CODE_FAILED:
                update_message = safe_dom.NodeList().append(
                    safe_dom.Text("""
                        There was an error updating enrollment/assessment
                        statistics. Here is the message:""")
                ).append(
                    safe_dom.Element('br')
                ).append(
                    safe_dom.Element('blockquote').add_child(
                        safe_dom.Element('pre').add_text('\n%s' % job.output)))
            else:
                update_message = safe_dom.Text(
                    'Enrollment and assessment statistics update started at %s'
                    ' and is running now. Please come back shortly.' %
                    job.updated_on.strftime(HUMAN_READABLE_TIME_FORMAT))

        subtemplate_values['stats_calculated'] = stats_calculated
        subtemplate_values['errors'] = errors
        subtemplate_values['update_message'] = update_message

        return jinja2.utils.Markup(self.get_template(
            'basic_analytics.html', [os.path.dirname(__file__)]
        ).render(subtemplate_values, autoescape=True))

    def get_analytics(self):
        """Renders course analytics view."""
        template_values = {}
        template_values['page_title'] = self.format_title('Analytics')

        all_jobs_have_finished = True

        basic_analytics_job = ComputeStudentStats(self.app_context).load()
        stats_html = self.get_markup_for_basic_analytics(basic_analytics_job)
        if (basic_analytics_job and
            basic_analytics_job.status_code != jobs.STATUS_CODE_COMPLETED):
            all_jobs_have_finished = False

        for callback in DashboardRegistry.analytics_handlers:
            handler = callback()
            handler.app_context = self.app_context
            handler.request = self.request
            handler.response = self.response

            job = handler.stats_computer(self.app_context).load()
            stats_html += handler.get_markup(job)

            if job and job.status_code != jobs.STATUS_CODE_COMPLETED:
                all_jobs_have_finished = False

        template_values['main_content'] = jinja2.utils.Markup(self.get_template(
            'analytics.html', [os.path.dirname(__file__)]
        ).render({
            'show_recalculate_button': all_jobs_have_finished,
            'stats_html': stats_html,
            'xsrf_token': self.create_xsrf_token('compute_student_stats'),
        }, autoescape=True))

        self.render_page(template_values)

    def post_compute_student_stats(self):
        """Submits a new student statistics calculation task."""
        job = ComputeStudentStats(self.app_context)
        job.submit()

        for callback in DashboardRegistry.analytics_handlers:
            job = callback().stats_computer(self.app_context)
            job.submit()

        self.redirect('/dashboard?action=analytics')


class ScoresAggregator(object):
    """Aggregates scores statistics."""

    def __init__(self):
        # We store all data as tuples keyed by the assessment type name. Each
        # tuple keeps:
        #     (student_count, sum(score))
        self.name_to_tuple = {}

    def visit(self, student):
        if student.scores:
            scores = transforms.loads(student.scores)
            for key in scores.keys():
                if key in self.name_to_tuple:
                    count = self.name_to_tuple[key][0]
                    score_sum = self.name_to_tuple[key][1]
                else:
                    count = 0
                    score_sum = 0
                self.name_to_tuple[key] = (
                    count + 1, score_sum + float(scores[key]))


class EnrollmentAggregator(object):
    """Aggregates enrollment statistics."""

    def __init__(self):
        self.enrolled = 0
        self.unenrolled = 0

    def visit(self, student):
        if student.is_enrolled:
            self.enrolled += 1
        else:
            self.unenrolled += 1


class ComputeStudentStats(jobs.DurableJob):
    """A job that computes student statistics."""

    def run(self):
        """Computes student statistics."""

        enrollment = EnrollmentAggregator()
        scores = ScoresAggregator()
        mapper = utils.QueryMapper(
            Student.all(), batch_size=500, report_every=1000)

        def map_fn(student):
            enrollment.visit(student)
            scores.visit(student)

        mapper.run(map_fn)

        data = {
            'enrollment': {
                'enrolled': enrollment.enrolled,
                'unenrolled': enrollment.unenrolled},
            'scores': scores.name_to_tuple}

        return data


class DashboardRegistry(object):
    """Holds registered handlers that produce HTML code for the dashboard."""
    analytics_handlers = []

    @classmethod
    def add_custom_analytics_section(cls, handler):
        """Adds handlers that provide additional data for the Analytics page."""
        if handler not in cls.analytics_handlers:
            existing_names = [h.name for h in cls.analytics_handlers]
            existing_names.append('enrollment')
            existing_names.append('scores')
            if handler.name in existing_names:
                raise Exception('Stats handler name %s is being duplicated.'
                                % handler.name)

            cls.analytics_handlers.append(handler)


custom_module = None


def register_module():
    """Registers this module in the registry."""

    dashboard_handlers = [('/dashboard', DashboardHandler)]

    global custom_module
    custom_module = custom_modules.Module(
        'Course Dashboard',
        'A set of pages for managing Course Builder course.',
        [], dashboard_handlers)
    return custom_module
