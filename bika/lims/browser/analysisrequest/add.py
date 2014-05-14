import json
import plone

from bika.lims import bikaMessageFactory as _
from bika.lims.browser import BrowserView
from bika.lims.browser.analysisrequest import AnalysisRequestViewView
from bika.lims.browser.bika_listing import BikaListingView
from bika.lims.content.analysisrequest import schema as AnalysisRequestSchema
from bika.lims.interfaces import IAnalysisRequestAddView
from bika.lims.utils import getHiddenAttributesForClass
from bika.lims.utils import t
from bika.lims.utils import tmpID
from bika.lims.utils.analysisrequest import create_analysisrequest
from bika.lims.utils.form import ajax_form_error
from magnitude import mg
from plone.app.layout.globals.interfaces import IViewView
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.utils import _createObjectByType, safe_unicode
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.component import getAdapter
from zope.interface import implements


class AnalysisRequestAddView(AnalysisRequestViewView):
    """ The main AR Add form
    """
    implements(IViewView, IAnalysisRequestAddView)
    #template = ViewPageTemplateFile("templates/ar_add.pt")
    template = ViewPageTemplateFile('templates/ar_add_by_row.pt')

    def __init__(self, context, request):
        AnalysisRequestViewView.__init__(self, context, request)
        self.came_from = "add"
        self.can_edit_sample = True
        self.can_edit_ar = True
        self.DryMatterService = self.context.bika_setup.getDryMatterService()
        request.set('disable_plone.rightcolumn', 1)
        self.ar_count = self.request.get('ar_count', 4)
        try:
            self.ar_count = int(self.ar_count)
        except:
            self.ar_count = 4

    def getHeaderFieldNames(self):
        #TODO Soft Code
        return ('Contact', 'CCContact', 'CCEmails', 'Client', 'Batch')

    def __call__(self):
        self.request.set('disable_border', 1)
        return self.template()

    def getContacts(self):
        adapter = getAdapter(self.context.aq_parent, name='getContacts')
        return adapter()

    def getWidgetVisibility(self):
        adapter = getAdapter(self.context, name='getWidgetVisibility')
        ret = adapter()
        ordered_ret = {}
        # respect schemaextender's re-ordering of fields, and
        # remove hidden attributes.
        hiddenattributes = getHiddenAttributesForClass('AnalysisRequest')
        schema_fields = [f.getName() for f in self.context.Schema().fields()]
        for mode, state_field_lists in ret.items():
            ordered_ret[mode] = {}
            for statename, state_fields in state_field_lists.items():
                ordered_ret[mode][statename] = \
                    [field for field in schema_fields
                     if field in state_fields
                     and field not in hiddenattributes]
        return ordered_ret

    def partitioned_services(self):
        bsc = getToolByName(self.context, 'bika_setup_catalog')
        ps = []
        for service in bsc(portal_type='AnalysisService'):
            service = service.getObject()
            if service.getPartitionSetup() \
                    or service.getSeparate():
                ps.append(service.UID())
        return json.dumps(ps)


class SecondaryARSampleInfo(BrowserView):
    """Return fieldnames and pre-digested values for Sample fields which
    javascript must disable/display while adding secondary ARs
    """

    def __init__(self, context, request):
        super(SecondaryARSampleInfo, self).__init__(context, request)
        self.context = context
        self.request = request

    def __call__(self):
        uid = self.request.get('Sample_uid')
        uc = getToolByName(self.context, "uid_catalog")
        sample = uc(UID=uid)[0].getObject()
        sample_schema = sample.Schema()
        adapter = getAdapter(self.context, name='getWidgetVisibility')
        wv = adapter()
        fieldnames = wv.get('secondary', {}).get('invisible', [])
        ret = []
        hiddenattributes = getHiddenAttributesForClass('AnalysisRequest')
        for fieldname in fieldnames:
            if fieldname in sample_schema:
                if fieldname in hiddenattributes:
                    continue
                fieldvalue = sample_schema[fieldname].getAccessor(sample)()
                if fieldvalue is None:
                    fieldvalue = ''
                if hasattr(fieldvalue, 'Title'):
                    fieldvalue = fieldvalue.Title()
                if hasattr(fieldvalue, 'year'):
                    fieldvalue = fieldvalue.strftime(self.date_format_short)
            else:
                fieldvalue = ''
            ret.append([fieldname, fieldvalue])
        return json.dumps(ret)


class ajaxExpandCategory(BikaListingView):
    """ ajax requests pull this view for insertion when category header
    rows are clicked/expanded. """
    template = ViewPageTemplateFile(
        "templates/analysisrequest_analysisservices.pt")

    def __call__(self):
        plone.protect.CheckAuthenticator(self.request.form)
        plone.protect.PostOnly(self.request.form)
        if hasattr(self.context, 'getRequestID'):
            self.came_from = "edit"
        return self.template()

    def bulk_discount_applies(self):
        client = None
        if self.context.portal_type == 'AnalysisRequest':
            client = self.context.aq_parent
        elif self.context.portal_type == 'Batch':
            client = self.context.getClient()
        elif self.context.portal_type == 'Client':
            client = self.context
        return client.getBulkDiscount() if client is not None else False

    def Services(self, poc, CategoryUID):
        """ return a list of services brains """
        bsc = getToolByName(self.context, 'bika_setup_catalog')
        services = bsc(portal_type="AnalysisService",
                       sort_on='sortable_title',
                       inactive_state='active',
                       getPointOfCapture=poc,
                       getCategoryUID=CategoryUID)
        return services


class ajaxAnalysisRequestSubmit():
    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self):

        form = self.request.form
        plone.protect.CheckAuthenticator(self.request.form)
        plone.protect.PostOnly(self.request.form)
        came_from = 'came_from' in form and form['came_from'] or 'add'
        wftool = getToolByName(self.context, 'portal_workflow')
        uc = getToolByName(self.context, 'uid_catalog')
        bsc = getToolByName(self.context, 'bika_setup_catalog')

        SamplingWorkflowEnabled = \
            self.context.bika_setup.getSamplingWorkflowEnabled()

        errors = {}

        form_parts = json.loads(self.request.form['parts'])

        #Get header values out, get values, exclude from any loops below
        headerFieldNames = ['Client', 'Client_uid', 'Contact', 'Contact_uid',
                            'CCContact', 'CCContact_uid', 'CCEmails', 
                            'Batch', 'Batch_uid']
        headers = {}
        for field in form.keys():
            if field in headerFieldNames:
                headers[field] = form[field]


        # First make a list of non-empty fieldNames
        fieldNames = []
        for arnum in range(int(form['ar_count'])):
            name = 'ar.%s' % arnum
            ar = form.get(name, None)
            if ar and 'Analyses' in ar.keys():
                fieldNames.append(arnum)

        if len(fieldNames) == 0:
            ajax_form_error(errors, message=t(_("No analyses have been selected")))
            return json.dumps({'errors':errors})

        # Now some basic validation
        required_fields = [field.getName() for field
                           in AnalysisRequestSchema.fields()
                           if field.required]

        for fieldName in fieldNames:
            formkey = "ar.%s" % fieldName
            ar = form[formkey]
            # Secondary ARs don't have sample fields present in the form data
            # if 'Sample_uid' in ar and ar['Sample_uid']:
            # adapter = getAdapter(self.context, name='getWidgetVisibility')
            #     wv = adapter().get('secondary', {}).get('invisible', [])
            #     required_fields = [x for x in required_fields if x not in wv]
            # check that required fields have values
            for field in required_fields:
                # This one is still special.
                if field in ['RequestID']:
                    continue
                    # And these are not required if this is a secondary AR
                if ar.get('Sample', '') != '' and field in [
                    'SamplingDate',
                    'SampleType'
                ]:
                    continue
                if (field in ar and not ar.get(field, '')):
                    ajax_form_error(errors, field, fieldName)
        # Return errors if there are any
        if errors:
            return json.dumps({'errors': errors})
        # Get the prices from the form data
        prices = form.get('Prices', None)
        # Initialize the Anlysis Request collection
        ARs = []
        # if a new profile is created automatically,
        # this flag triggers the status message
        new_profile = None
        # The actual submission
        for fieldName in fieldNames:
            # Get partitions from the form data
            if form_parts:
                partitions = form_parts[str(fieldName)]
            else:
                partitions = []
            # Get the form data using the appropriate form key
            formkey = "ar.%s" % fieldName
            values = form[formkey].copy()
            # resolved values is formatted as acceptable by archetypes
            # widget machines
            resolved_values = {}
            for k, v in values.items():
                # Analyses, we handle that specially.
                if k == 'Analyses':
                    continue
                if "%s_uid" % k in values:
                    v = values["%s_uid" % k]
                    if v and "," in v:
                        v = v.split(",")
                    resolved_values[k] = values["%s_uid" % k]
                else:
                    resolved_values[k] = values[k]
            # Get the analyses from the form data
            analyses = values["Analyses"]
            # Gather the specifications from the form data
            specifications = {}
            if len(values.get("min", [])):
                for n, service_uid in enumerate(analyses):
                    specifications[service_uid] = {
                        "min": values["min"][n],
                        "max": values["max"][n],
                        "error": values["error"][n]
                    }
            # Selecting a template sets the hidden 'parts' field to template values.
            # Selecting a profile will allow ar_add.js to fill in the parts field.
            # The result is the same once we are here.
            if not partitions:
                partitions = [{
                    'services': [],
                    'container': None,
                    'preservation': '',
                    'separate': False
                }]
            # Apply DefaultContainerType to partitions without a container
            default_container_type = resolved_values.get(
                'DefaultContainerType', None
            )
            if default_container_type:
                container_type = bsc(UID=default_container_type)[0].getObject()
                containers = container_type.getcontainers()
                for partition in partitions:
                    if not partition.get(container, None):
                        partition['container'] = containers
            # Retrieve the catalogue reference to the client
            #client = uc(UID=resolved_values['Client'])[0].getObject()
            client = uc(UID=headers['Client_uid'])[0].getObject()
            # Create the Analysis Request
            ar = create_analysisrequest(
                client,
                self.request,
                resolved_values,
                analyses,
                partitions,
                specifications,
                prices
            )
            #Add Headers
            for fieldName in headers.keys():
                if headers[fieldName] != '' and not fieldName.endswith('_uid'):
                    if headers.get(fieldName+'_uid'):
                        field = ar.Schema()[fieldName];
                        mutator = field.getMutator(ar)
                        uid = headers[fieldName+'_uid']
                        obj = uc(UID=uid)[0].getObject()
                        mutator(obj)
                    else:
                        ar.edit(fieldName=headers[fieldName])

            # Add the created analysis request to the list
            ARs.append(ar.getId())
        # Display the appropriate message after creation
        if len(ARs) > 1:
            message = _("Analysis requests ${ARs} were successfully created.",
                        mapping={'ARs': safe_unicode(', '.join(ARs))})
        else:
            message = _("Analysis request ${AR} was successfully created.",
                        mapping={'AR': safe_unicode(ARs[0])})
        self.context.plone_utils.addPortalMessage(message, 'info')
        # Automatic label printing
        # Won't print labels for Register on Secondary ARs
        new_ars = None
        if came_from == 'add':
            new_ars = [ar for ar in ARs if ar[-2:] == '01']
        if 'register' in self.context.bika_setup.getAutoPrintLabels() and new_ars:
            return json.dumps({
                'success': message,
                'labels': new_ars,
                'labelsize': self.context.bika_setup.getAutoLabelSize()
            })
        else:
            return json.dumps({'success': message})
